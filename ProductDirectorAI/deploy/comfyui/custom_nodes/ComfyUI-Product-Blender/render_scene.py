"""Run with Blender --background --factory-startup --python this.py -- config.json."""
import bpy, json, math, sys
from pathlib import Path
from mathutils import Vector, Matrix

C=json.loads(Path(sys.argv[sys.argv.index('--')+1]).read_text(encoding='utf-8'))
OUT=Path(C['outdir']); PLAN=C['plan']; scene=bpy.context.scene
bpy.context.preferences.filepaths.save_version=0
bpy.ops.object.select_all(action='SELECT'); bpy.ops.object.delete(use_global=False)

def report(message,progress):print('STUDIO:'+json.dumps(dict(message=message,progress=progress),ensure_ascii=False),flush=True)
def material(name,color,metallic=0,roughness=.42):
    m=bpy.data.materials.new(name); m.diffuse_color=(*color,1); m.use_nodes=True
    bs=m.node_tree.nodes.get('Principled BSDF'); bs.inputs['Base Color'].default_value=(*color,1); bs.inputs['Metallic'].default_value=metallic; bs.inputs['Roughness'].default_value=roughness
    return m
ink=material('Graphite',(.045,.06,.075),.3); silver=material('Pearl',(.6,.66,.69),.25); teal=material('Signal',(.08,.55,.45),.25)
env=[]
def cube(name,loc,scale,mat,bevel=.08,environment=False):
    bpy.ops.mesh.primitive_cube_add(size=1,location=loc); o=bpy.context.object; o.name=name; o.dimensions=scale
    bpy.ops.object.transform_apply(location=False,rotation=False,scale=True)
    if bevel:
        mod=o.modifiers.new('Soft edges','BEVEL'); mod.width=bevel; mod.segments=3
        o.modifiers.new('Weighted normals','WEIGHTED_NORMAL')
    o.data.materials.append(mat)
    if environment:env.append(o)
    return o
def cylinder(name,loc,radius,depth,mat,rot=(0,0,0)):
    bpy.ops.mesh.primitive_cylinder_add(vertices=64,radius=radius,depth=depth,location=loc,rotation=rot)
    o=bpy.context.object;o.name=name;o.data.materials.append(mat)
    mod=o.modifiers.new('Edge','BEVEL');mod.width=.025;mod.segments=3
    o.modifiers.new('Normals','WEIGHTED_NORMAL')
    for p in o.data.polygons:p.use_smooth=True
    return o

report('导入产品资产',40)
asset=C.get('asset')
if asset:
    extension=Path(asset).suffix.lower()
    if extension=='.glb':bpy.ops.import_scene.gltf(filepath=asset)
    elif extension=='.obj':bpy.ops.wm.obj_import(filepath=asset)
    elif extension=='.stl':bpy.ops.wm.stl_import(filepath=asset)
    else:raise ValueError('Unsupported model')
else:
    cube('Demo smart speaker',(0,0,0),(1.25,.72,1.85),ink,.18)
    cube('Front panel',(0,-.37,0),(1.05,.06,1.57),silver,.12)
    for z,r in [(-.35,.32),(.38,.23)]:
        cylinder('Speaker',(0,-.425,z),r,.045,ink,(math.pi/2,0,0))
        cylinder('Cone',(0,-.455,z),r*.42,.025,teal,(math.pi/2,0,0))
    cube('Status light',(0,-.42,.7),(.28,.025,.025),teal,.008)
meshes=[o for o in scene.objects if o.type=='MESH']
if not meshes:raise ValueError('模型不含可渲染的网格')
bpy.context.view_layer.update()
points=[o.matrix_world@Vector(v) for o in meshes for v in o.bound_box]
lo=Vector([min(v[i] for v in points) for i in range(3)]);hi=Vector([max(v[i] for v in points) for i in range(3)])
center=(lo+hi)/2; span=max(hi-lo)
if span<1e-8:raise ValueError('模型尺寸为零')
longest=C.get('size_cm',35)/100
scale=longest/span; height=(hi.z-lo.z)*scale
for o in meshes:
    transform=Matrix.Scale(scale,4)@Matrix.Translation(-center)@o.matrix_world
    o.data=o.data.copy();o.data.transform(transform);o.parent=None;o.matrix_parent_inverse=Matrix.Identity(4);o.matrix_world=Matrix.Identity(4)
    o.animation_data_clear()
    if not o.data.materials:o.data.materials.append(silver)
for o in list(scene.objects):
    if o not in meshes:bpy.data.objects.remove(o,do_unlink=True)
root=bpy.data.objects.new('PRODUCT · rotation pivot',None);scene.collection.objects.link(root)
root.location=(0,0,height/2)
for o in meshes:o.parent=root
if C.get('photo_texture')=='sheet' and C.get('reference_image'):
    # Photo projection for this documented front/side/back product-sheet layout.
    # The source image is sampled directly; no generated replacement image is used.
    photo=bpy.data.images.load(C['reference_image']);photo.pack()
    def photo_material(name):
        mat=material(name,(.07,.07,.07),0,.85)
        nodes=mat.node_tree.nodes;bs=nodes.get('Principled BSDF');tex=nodes.new('ShaderNodeTexImage');tex.image=photo
        mat.node_tree.links.new(tex.outputs['Color'],bs.inputs['Base Color'])
        bs.inputs['Specular IOR Level'].default_value=.05
        emission=nodes.new('ShaderNodeEmission');emission.inputs['Strength'].default_value=.85
        mat.node_tree.links.new(tex.outputs['Color'],emission.inputs['Color'])
        mix=nodes.new('ShaderNodeMixShader');mix.inputs[0].default_value=.75
        mat.node_tree.links.new(bs.outputs[0],mix.inputs[1]);mat.node_tree.links.new(emission.outputs[0],mix.inputs[2])
        mat.node_tree.links.new(mix.outputs[0],nodes.get('Material Output').inputs['Surface'])
        return mat
    front_mat=photo_material('Front photograph projection');back_mat=photo_material('Rear photograph projection')
    side_mat=material('Black ABS edge',(.018,.019,.021),.05,.42)
    minx=(lo.x-center.x)*scale;maxx=(hi.x-center.x)*scale;minz=(lo.z-center.z)*scale;maxz=(hi.z-center.z)*scale
    for o in meshes:
        o.data.materials.clear()
        for mat in (front_mat,back_mat,side_mat):o.data.materials.append(mat)
        uv=o.data.uv_layers.new(name='Reference projection')
        import numpy as np
        mesh=o.data
        coords=np.empty(len(mesh.vertices)*3,dtype=np.float32)
        mesh.vertices.foreach_get('co',coords)
        coords=coords.reshape(-1,3)
        normals=np.empty(len(mesh.polygons)*3,dtype=np.float32)
        mesh.polygons.foreach_get('normal',normals)
        normal_y=normals.reshape(-1,3)[:,1]
        indices=np.where(normal_y>.18,1,np.where(normal_y<-.18,0,2)).astype(np.int32)
        mesh.polygons.foreach_set('material_index',indices)
        mesh.polygons.foreach_set('use_smooth',np.ones(len(mesh.polygons),dtype=np.bool_))
        counts=np.empty(len(mesh.polygons),dtype=np.int32)
        mesh.polygons.foreach_get('loop_total',counts)
        rear=np.repeat(normal_y>.18,counts)
        vertex_ids=np.empty(len(mesh.loops),dtype=np.int32)
        mesh.loops.foreach_get('vertex_index',vertex_ids)
        loop_coords=coords[vertex_ids]
        x=(loop_coords[:,0]-minx)/max(1e-9,maxx-minx)
        z=(loop_coords[:,2]-minz)/max(1e-9,maxz-minz)
        px=np.where(rear,1454-562*x,50+572*x)
        py=np.where(rear,20+568*(1-z),22+565*(1-z))
        values=np.column_stack((px/1536,1-py/1024)).astype(np.float32)
        uv.data.foreach_set('uv',values.ravel())
        mesh.update()
        report('产品贴图已完成',42)
bpy.ops.object.select_all(action='DESELECT')
for o in meshes:o.select_set(True)
bpy.context.view_layer.objects.active=meshes[0]
bpy.ops.export_scene.gltf(filepath=str(OUT/'product.glb'),export_format='GLB',use_selection=True,export_animations=False)

warm=PLAN['warm']
floor=material('Floor',(.29,.24,.19) if PLAN['scene']!='studio' else (.16,.19,.21),0,.65)
wall=material('Warm plaster',(.69,.65,.56) if warm else (.5,.6,.66),0,.8)
fabric=material('Linen',(.32,.42,.40),0,.9)
wood=material('Oak',(.42,.25,.12),0,.6)
cube('Floor',(0,0,-.13),(200,200,.2),floor,.02,True)
if PLAN['scene']!='studio':
    cube('Back wall',(0,2.8,2.5),(12,.2,5),wall,.02,True)
    cube('Side wall',(-4,0,2.5),(.2,5.6,5),wall,.02,True)
    if PLAN['scene']=='living':
        cube('Sofa seat',(-2,1.25,.48),(2.35,1,.55),fabric,.18,True)
        cube('Sofa back',(-2,1.65,.99),(2.35,.3,1),fabric,.15,True)
        for x in (-3.15,-.85):cube('Armrest',(x,1.25,.73),(.24,1.1,.7),fabric,.1,True)
        cube('Side table',(2,1,.7),(1.15,.8,.1),wood,.04,True)
        for x in (1.55,2.45):cube('Table leg',(x,1,.32),(.08,.6,.64),ink,.015,True)
    else:
        cube('Bed base',(-2.3,.8,.3),(2,2.9,.5),wood,.1,True)
        cube('Mattress',(-2.3,.8,.64),(1.96,2.85,.28),fabric,.15,True)
        cube('Pillow',(-2.3,1.7,.87),(1.3,.7,.18),wall,.14,True)
        cube('Desk',(2,1.4,.95),(1.6,.8,.1),wood,.04,True)
        for x in (1.3,2.7):cube('Desk leg',(x,1.4,.45),(.1,.7,.9),ink,.015,True)
if PLAN['mounted']:
    if PLAN['scene']=='studio':cube('Mounting wall',(0,2.8,2.5),(12,.2,5),wall,.02,True)
    root.location.y=2.66-(hi.y-center.y)*scale;root.location.z=1.45
target=Vector(root.location)
world=bpy.data.worlds.new('Studio world');scene.world=world;world.use_nodes=True
world.node_tree.nodes['Background'].inputs[0].default_value=(.19,.23,.28,1)
world.node_tree.nodes['Background'].inputs[1].default_value=.45
def aim(o,point):o.rotation_euler=(Vector(point)-o.location).to_track_quat('-Z','Y').to_euler()
def area(name,loc,power,size,color):
    data=bpy.data.lights.new(name,'AREA');data.energy=power;data.shape='DISK';data.size=size;data.color=color
    o=bpy.data.objects.new(name,data);scene.collection.objects.link(o);o.location=loc;aim(o,target)
area('Key',(-3,-4,6),1000,5,(1,.83,.67) if warm else (.75,.86,1))
area('Fill',(4,-2,3),650,4,(.72,.83,1))
area('Rim',(1,3,5),1300,3,(1,.94,.85))
data=bpy.data.cameras.new('Camera');camera=bpy.data.objects.new('Camera',data);scene.collection.objects.link(camera);scene.camera=camera
data.lens=48;data.clip_end=300
scene.render.engine='CYCLES';scene.cycles.samples=24 if C['quality']=='preview' else 48;scene.cycles.use_denoising=True
try:
    prefs=bpy.context.preferences.addons['cycles'].preferences;prefs.compute_device_type='OPTIX';prefs.get_devices()
    gpu=False
    for device in prefs.devices:
        device.use=device.type!='CPU';gpu=gpu or device.use
    if gpu:scene.cycles.device='GPU'
except Exception:pass
base={'preview':480,'720':1280,'1080':1920}[C['quality']]
a,b=map(int,C['ratio'].split(':'));scene.render.resolution_x=round(base*a/max(a,b)/2)*2;scene.render.resolution_y=round(base*b/max(a,b)/2)*2
scene.render.resolution_percentage=100;scene.render.fps=C['fps'];scene.frame_start=1;scene.frame_end=C['seconds']*C['fps']
scene.view_settings.view_transform='AgX'
scene.render.image_settings.file_format='PNG';scene.render.image_settings.color_mode='RGB'
aspect=scene.render.resolution_x/scene.render.resolution_y
distance=max(6,4/aspect)*longest/2
if C.get('framing')=='room':distance=max(3.5,distance)
def pose(offset):camera.location=target+Vector(offset);aim(camera,target)
def still(name):
    scene.render.image_settings.file_format='PNG';scene.render.filepath=str(OUT/name);bpy.ops.render.render(write_still=True)
def visible(enabled):
    for o in env:o.hide_render=not enabled
def clear_animation():
    camera.animation_data_clear();root.animation_data_clear();root.rotation_euler=(0,0,0);scene.frame_set(1)
if 'views' in C['outputs']:
    visible(False);data.type='ORTHO';data.ortho_scale=max(1.4,1.4/aspect)*longest
    offsets={'front':(0,-6,0),'back':(0,6,0),'left':(-6,0,0),'right':(6,0,0),'top':(0,-.001,6),'bottom':(0,-.001,-6),'angle':(4,-6,3)}
    for i,(name,offset) in enumerate(offsets.items()):
        report('正在渲染产品视图 '+name,43+i*3);pose(offset);still('view-'+name+'.png')
    visible(True);data.type='PERSP'
pose((distance*.45,-distance,distance*.3))
if 'still' in C['outputs']:report('正在渲染场景图片',65);still('scene.png')
def video(name,kind):
    clear_animation();data.type='PERSP';visible(kind!='turntable')
    pose((distance*.4,-distance,distance*.23))
    for frame in range(1,scene.frame_end+1):
        t=(frame-1)/max(1,scene.frame_end-1)
        if kind=='turntable':
            root.rotation_euler.z=2*math.pi*(frame-1)/scene.frame_end;root.keyframe_insert(data_path='rotation_euler',frame=frame)
        else:
            motion=PLAN['motion']
            if motion=='push':pose((distance*.28*(1-.25*t),-distance*(1-.3*t),distance*.2))
            elif motion=='pan':pose((distance*(.4-.8*t),-distance,distance*.22))
            else:
                angle=math.radians(-25+50*t);pose((math.sin(angle)*distance,-math.cos(angle)*distance,distance*.24))
            camera.keyframe_insert(data_path='location',frame=frame);camera.keyframe_insert(data_path='rotation_euler',frame=frame)
    if hasattr(scene.render.image_settings,'media_type'):scene.render.image_settings.media_type='VIDEO'
    scene.render.image_settings.file_format='FFMPEG';scene.render.ffmpeg.format='MPEG4';scene.render.ffmpeg.codec='H264';scene.render.ffmpeg.constant_rate_factor='MEDIUM';scene.render.filepath=str(OUT/name)
    scene.frame_set(1)
    bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'scene.blend'))
    count=sum(x in C['outputs'] for x in ('turntable','video'))
    index=1 if kind=='scene' and 'turntable' in C['outputs'] else 0
    def progress(s,*args):report('正在渲染 '+name+' · '+str(s.frame_current)+' / '+str(s.frame_end),70+int(27*(index+s.frame_current/s.frame_end)/count))
    bpy.app.handlers.render_write.append(progress)
    try:bpy.ops.render.render(animation=True)
    finally:bpy.app.handlers.render_write.remove(progress)
if 'turntable' in C['outputs']:report('正在生成 360° 视频',70);video('turntable.mp4','turntable')
if 'video' in C['outputs']:report('正在生成场景视频',70);video('scene.mp4','scene')
if not any(x in C['outputs'] for x in ['video','turntable']):
    clear_animation();pose((distance*.45,-distance,distance*.3));bpy.ops.wm.save_as_mainfile(filepath=str(OUT/'scene.blend'))
report('渲染完成，正在检查输出文件',99)
