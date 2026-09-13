// One SDK-only lookahead slot. Preparing it never touches the active player.
export function createNextVideoQueue(game, changed, delay = () => new Promise(resolve=>setTimeout(resolve,1000))) {
  let generation=0, busy=false, disposed=false;
  const publish=(token,state)=>{if(!disposed && token===generation)changed(state);};
  return {
    get busy(){return busy;},
    clear(){generation++;changed({status:'idle',busy});},
    dispose(){disposed=true;generation++;},
    async start({topic,exclude,character,render_language}) {
      if(busy || disposed)return;
      busy=true;const token=++generation;
      publish(token,{status:'searching',busy:true});
      try {
        const found=await game.media.request('discover',{topic,exclude});
        if(disposed || game.disposed || token!==generation)return;
        if(!found.video){publish(token,{status:'empty',busy:true});return;}
        const title=found.video.title;
        publish(token,{status:'preparing',title,busy:true});
        const job=await game.media.request('prepare',{url:found.video.url,source:'discovery',lanlan_name:character,render_language});
        if(job.confirmation_required || !job.id)throw Error('Invalid automatic preparation');
        // Finish tracking an already started job even when the selection changes.
        // This keeps the single preparation slot occupied until the backend frees it.
        while(!disposed && !game.disposed) {
          const state=await game.media.request('preparation',{job:job.id});
          if(['error','cancelled'].includes(state.status)) {
            // A completed attempt is excluded; a failed submission can retry.
            changed({candidate:found.video.bvid});
            throw Error(state.stage_key || 'prepareFailed');
          }
          publish(token,{status:'preparing',title,stage:state.stage_key,progress:state.progress,busy:true});
          if(state.status==='ready' && state.persistence_complete!==false) {
            if(!disposed && !game.disposed)changed({candidate:found.video.bvid});
            const history=await game.media.request('history');
            if(!disposed && !game.disposed && token!==generation)changed({history});
            const row=history.analyses.find(item=>item.job===job.id && item.status==='ready');
            if(row){publish(token,{status:'ready',title,row,history,busy:true});return;}
            throw Error('prepareFailed');
          }
          await delay();
        }
      } catch(error) {publish(token,{status:'error',error:error.message,busy:true});}
      finally {busy=false;if(!disposed)changed({busy:false,released:true});}
    }
  };
}
