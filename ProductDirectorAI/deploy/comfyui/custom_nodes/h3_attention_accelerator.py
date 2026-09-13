"""Workflow-scoped adapter for official thu-ml/SageAttention (Apache-2.0)."""
import logging
import comfy.ldm.modules.attention as attention
class H3AttentionAccelerator:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'model': ('MODEL',), 'enabled': ('BOOLEAN', {'default': True})}}
    RETURN_TYPES=('MODEL',)
    FUNCTION='patch'
    CATEGORY='Product Studio/Acceleration'
    def patch(self,model,enabled):
        if not enabled:return (model,)
        if not attention.SAGE_ATTENTION_IS_AVAILABLE:raise RuntimeError('SageAttention is not installed')
        clone=model.clone()
        impl=getattr(attention.attention_sage,'__wrapped__',attention.attention_sage)
        def override(current,*args,**kwargs):return impl(*args,**kwargs)
        clone.model_options.setdefault('transformer_options',{})['optimized_attention_override']=override
        logging.info('H3 acceleration: official SageAttention 1.0.6 enabled for this model only')
        return (clone,)
NODE_CLASS_MAPPINGS={'H3AttentionAccelerator':H3AttentionAccelerator}
NODE_DISPLAY_NAME_MAPPINGS={'H3AttentionAccelerator':'H3 注意力加速（SageAttention）'}
