"""Local MESH -> Blender render bridge. No network services or generated code execution."""
import json, subprocess, time, uuid, os, shutil
from pathlib import Path
import numpy as np
import torch
from PIL import Image
import folder_paths
import comfy.model_management as mm
from comfy_extras.nodes_save_3d import mesh_item_to_glb_bytes
from comfy_api.input_impl import VideoFromFile

class ProductBlenderRender:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            'mesh': ('MESH',), 'reference_image': ('IMAGE',),
            'scene': (['studio','living','bedroom'],),
            'motion': (['pan','push','orbit'],),
            'size_cm': ('FLOAT', {'default':35,'min':1,'max':300}),
            'seconds': ('INT', {'default':4,'min':1,'max':15}),
            'quality': (['preview','720','1080'],),
            'photo_texture': (['sheet','none'],),
            'framing': (['product','room'],),
        }, 'optional': {
            'render_preview_video': ('BOOLEAN', {'default':True}),
        }}
    RETURN_TYPES=('VIDEO','IMAGE','IMAGE','IMAGE','STRING')
    RETURN_NAMES=('Blender视频','正面','背面','45度','Blender工程路径')
    FUNCTION='render'
    CATEGORY='Product Studio/Blender'
    OUTPUT_NODE=True

    def render(self,mesh,reference_image,scene,motion,size_cm,seconds,quality,photo_texture,framing,render_preview_video=True):
        blender=Path(os.environ.get('PRODUCTDIRECTOR_BLENDER') or shutil.which('blender') or '__missing_blender__')
        if not blender.is_file():raise RuntimeError('未找到 Blender，请加入 PATH 或设置 PRODUCTDIRECTOR_BLENDER。')
        sub=Path('Product-Blender')/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6])
        out=Path(folder_paths.get_output_directory())/sub;out.mkdir(parents=True)
        blob=mesh_item_to_glb_bytes(mesh,0)
        if not blob:raise ValueError('3D 网格为空')
        (out/'input.glb').write_bytes(blob)
        Image.fromarray((reference_image[0].detach().cpu().clamp(0,1).numpy()*255).astype(np.uint8)).save(out/'reference.png')
        config={'asset':str(out/'input.glb'),'outdir':str(out),'reference_image':str(out/'reference.png'),
                'plan':{'scene':scene,'motion':motion,'warm':True,'mounted':True},
                'ratio':'9:16','quality':quality,'seconds':seconds,'fps':24,
                'outputs':['views','still','video'] if render_preview_video else ['views'],'size_cm':size_cm,'photo_texture':photo_texture,'framing':framing}
        (out/'config.json').write_text(json.dumps(config),encoding='utf-8')
        mm.unload_all_models();mm.soft_empty_cache()
        command=[str(blender),'--background','--factory-startup','--python',str(Path(__file__).with_name('render_scene.py')),'--',str(out/'config.json')]
        with (out/'render.log').open('w',encoding='utf-8') as log:
            proc=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                deadline=time.monotonic()+3600
                while proc.poll() is None:
                    mm.throw_exception_if_processing_interrupted()
                    if time.monotonic()>deadline:raise TimeoutError('Blender 渲染超过一小时')
                    time.sleep(.5)
            except BaseException:
                proc.kill();proc.wait();raise
        video=out/'scene.mp4'
        if proc.returncode or (render_preview_video and not video.is_file()):raise RuntimeError('Blender 渲染失败：'+str(out/'render.log'))
        def img(name):
            with Image.open(out/name) as im:return torch.from_numpy(np.array(im.convert('RGB')).astype(np.float32)/255)[None]
        return {'ui':{'images':[{'filename':'scene.mp4' if render_preview_video else 'view-front.png','subfolder':sub.as_posix(),'type':'output'}],'animated':[render_preview_video]},
                'result':(VideoFromFile(str(video)) if render_preview_video else None,img('view-front.png'),img('view-back.png'),img('view-angle.png'),str(out/'scene.blend'))}

NODE_CLASS_MAPPINGS={'ProductBlenderRender':ProductBlenderRender}
NODE_DISPLAY_NAME_MAPPINGS={'ProductBlenderRender':'Blender 产品场景渲染（云端）'}


class ProductBlenderCached(ProductBlenderRender):
    """Persist reference renders; lazy mesh prevents upstream reconstruction on hits."""
    @classmethod
    def INPUT_TYPES(cls):
        import copy
        spec=copy.deepcopy(super().INPUT_TYPES())
        spec['required']['mesh']=('MESH',{'lazy':True})
        spec['optional']['render_preview_video']=('BOOLEAN',{'default':False})
        spec['optional']['cache_mode']=(['auto','rebuild'],{'default':'auto'})
        spec['hidden']={'prompt':'PROMPT','unique_id':'UNIQUE_ID'}
        return spec

    @classmethod
    def IS_CHANGED(cls,cache_mode='auto',**kwargs):
        if cache_mode=='rebuild':return float('nan')
        import hashlib
        files=[Path(__file__),Path(__file__).with_name('render_scene.py')]
        return hashlib.sha256(b''.join(p.read_bytes() for p in files)).hexdigest()

    @staticmethod
    def cache_details(reference_image,prompt,unique_id,params):
        import hashlib
        node=prompt[str(unique_id)]
        def recipe(nid,seen):
            nid=str(nid)
            if nid in seen:raise ValueError('Cycle in geometry graph')
            n=prompt[nid]; inputs={}
            for key,value in n['inputs'].items():
                if isinstance(value,list) and len(value)==2 and str(value[0]) in prompt:
                    inputs[key]={'node':recipe(value[0],seen|{nid}),'slot':value[1]}
                elif key not in ['video-preview']:
                    inputs[key]=value
                    if key=='ckpt_name':
                        path=folder_paths.get_full_path('checkpoints',value)
                        if path:
                            st=Path(path).stat();inputs['checkpoint_stat']=[st.st_size,st.st_mtime_ns]
            return {'class_type':n['class_type'],'inputs':inputs}
        source=node['inputs']['mesh'][0]
        digest=hashlib.sha256()
        im=reference_image.detach().cpu().contiguous()
        digest.update(str(tuple(im.shape)).encode());digest.update(im.numpy().tobytes())
        payload={'schema':1,'geometry':recipe(source,set()),'render':params}
        digest.update(json.dumps(payload,sort_keys=True,ensure_ascii=False).encode())
        digest.update(Path(__file__).with_name('render_scene.py').read_bytes())
        key=digest.hexdigest()
        root=Path(folder_paths.get_output_directory())/'Product-Blender-Cache';root.mkdir(exist_ok=True)
        return root/(key+'.json'),key

    @staticmethod
    def cached_folder(path,render_preview_video):
        try:
            data=json.loads(path.read_text());out=Path(data['output_directory'])
            required=['input.glb','product.glb','scene.blend','view-front.png','view-back.png','view-angle.png']
            if render_preview_video:required.append('scene.mp4')
            return out if all((out/name).is_file() and (out/name).stat().st_size>0 for name in required) else None
        except (OSError,ValueError,KeyError):return None

    @staticmethod
    def parameters(scene,motion,size_cm,seconds,quality,photo_texture,framing,render_preview_video):
        return dict(scene=scene,motion=motion,size_cm=size_cm,seconds=seconds,quality=quality,photo_texture=photo_texture,framing=framing,render_preview_video=render_preview_video)

    def check_lazy_status(self,reference_image,scene,motion,size_cm,seconds,quality,photo_texture,framing,prompt,unique_id,mesh=None,render_preview_video=False,cache_mode='auto'):
        params=self.parameters(scene,motion,size_cm,seconds,quality,photo_texture,framing,render_preview_video)
        path,key=self.cache_details(reference_image,prompt,unique_id,params)
        if cache_mode=='auto' and self.cached_folder(path,render_preview_video):return []
        return ['mesh'] if mesh is None else []

    def render(self,reference_image,scene,motion,size_cm,seconds,quality,photo_texture,framing,prompt,unique_id,mesh=None,render_preview_video=False,cache_mode='auto'):
        import logging
        params=self.parameters(scene,motion,size_cm,seconds,quality,photo_texture,framing,render_preview_video)
        path,key=self.cache_details(reference_image,prompt,unique_id,params)
        out=self.cached_folder(path,render_preview_video) if cache_mode=='auto' else None
        if out:
            logging.info('Product Blender persistent cache HIT %s: skipped geometry and rendering',key[:12])
            def img(name):
                with Image.open(out/name) as im:return torch.from_numpy(np.array(im.convert('RGB')).astype(np.float32)/255)[None]
            sub=out.relative_to(Path(folder_paths.get_output_directory())).as_posix()
            return {'ui':{'images':[{'filename':'scene.mp4' if render_preview_video else 'view-front.png','subfolder':sub,'type':'output'}],'animated':[render_preview_video]},'result':(VideoFromFile(str(out/'scene.mp4')) if render_preview_video else None,img('view-front.png'),img('view-back.png'),img('view-angle.png'),str(out/'scene.blend'))}
        if mesh is None:raise RuntimeError('Geometry was not supplied on a cache miss')
        result=super().render(mesh=mesh,reference_image=reference_image,**params)
        out=Path(result['result'][4]).parent
        temp=path.with_suffix('.tmp')
        temp.write_text(json.dumps({'key':key,'output_directory':str(out),'parameters':params},ensure_ascii=False),encoding='utf-8');temp.replace(path)
        logging.info('Product Blender persistent cache SAVED %s',key[:12])
        return result

NODE_CLASS_MAPPINGS['ProductBlenderCached']=ProductBlenderCached
NODE_DISPLAY_NAME_MAPPINGS['ProductBlenderCached']='产品建模与 Blender 自动复用'
