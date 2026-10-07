// Trusted SDK avatar provider, symmetric with vrm-host.mjs.
export function create(container) {
  const app = new PIXI.Application({backgroundAlpha:0,antialias:true,width:280,height:320});
  container.append(app.view);
  let model = null, disposed = false, mouthLevel = 0;
  return {
    async setModel(config) {
      const next = await PIXI.live2d.Live2DModel.from(config.path,{autoInteract:false});
      if (disposed) {next.destroy();return;}
      model?.destroy();model=next;app.stage.addChild(model);
      const core = next.internalModel.coreModel;
      const mouth = ['ParamMouthOpenY','ParamMouthOpen','ParamA','ParamO'].find(id=>{
        try {return typeof core.getParameterIndex === 'function' && core.getParameterIndex(id)>=0;}
        catch (_) {return false;}
      });
      next.internalModel.on('beforeModelUpdate',()=>{if(mouth)core.setParameterValueById(mouth,mouthLevel);});
    },
    resize({width,height}) {
      app.renderer.resize(width,height);
      if (!model) return;
      const scale=Math.min(width/model.internalModel.width,height/model.internalModel.height);
      model.scale.set(scale);model.anchor.set(.5,1);model.position.set(width/2,height);
    },
    focus({x,y}) {model?.focus(x,y);},
    setEmotion(name) {
      const expressions=model?.internalModel?.settings?.expressions || [];
      const match=expressions.find(item=>/happy|smile|笑/i.test(item.Name || item.name || ''));
      if(name==='happy' && match)model.expression(match.Name || match.name);
      else if(name==='neutral')model?.internalModel?.motionManager?.expressionManager?.resetExpression?.();
    },
    mouth(level) {mouthLevel=level;},
    pause(){app.stop();},resume(){app.start();},getState(){return {ready:!!model};},
    dispose(){disposed=true;app.destroy(true,{children:true,texture:true,baseTexture:true});},
  };
}
