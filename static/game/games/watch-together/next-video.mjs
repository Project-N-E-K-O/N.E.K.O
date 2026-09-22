// One SDK-only lookahead slot. Preparing it never touches the active player.
const MAX_CANDIDATE_FAILURES=2;
export function createNextVideoQueue(game, changed, delay = () => new Promise(resolve=>setTimeout(resolve,1000))) {
  let generation=0, busy=false, disposed=false;
  const failures=new Map();
  const publish=(token,state)=>{if(!disposed && token===generation)changed(state);};
  // A transient failure may retry once; a candidate that keeps failing is excluded.
  const failed=bvid=>{
    if(!bvid || disposed || game.disposed)return;
    const count=(failures.get(bvid) || 0)+1;
    failures.delete(bvid);
    if(count>=MAX_CANDIDATE_FAILURES){changed({candidate:bvid});return;}
    failures.set(bvid,count);
    if(failures.size>128)failures.delete(failures.keys().next().value);
  };
  return {
    get busy(){return busy;},
    clear(){generation++;changed({status:'idle',busy});},
    dispose(){disposed=true;generation++;},
    async start({topic,exclude,character,render_language}) {
      if(busy || disposed)return;
      busy=true;const token=++generation;
      let candidate=null;
      publish(token,{status:'searching',busy:true});
      try {
        const found=await game.media.request('discover',{topic,exclude});
        if(disposed || game.disposed || token!==generation)return;
        if(!found.video){publish(token,{status:'empty',busy:true});return;}
        const title=found.video.title;
        candidate=found.video.bvid || null;
        publish(token,{status:'preparing',title,busy:true});
        const job=await game.media.request('prepare',{url:found.video.url,source:'discovery',lanlan_name:character,render_language});
        if(job.confirmation_required || !job.id)throw Error('Invalid automatic preparation');
        // Finish tracking an already started job even when the selection changes.
        // This keeps the single preparation slot occupied until the backend frees it.
        while(!disposed && !game.disposed) {
          const state=await game.media.request('preparation',{job:job.id});
          if(['error','cancelled'].includes(state.status)) {
            throw Error(state.stage_key || 'prepareFailed');
          }
          publish(token,{status:'preparing',title,stage:state.stage_key,progress:state.progress,busy:true});
          if(state.status==='ready' && state.persistence_complete!==false) {
            // A completed attempt is excluded at once, and never counted as a failure.
            if(!disposed && !game.disposed)changed({candidate:found.video.bvid});
            candidate=null;
            const history=await game.media.request('history');
            if(!disposed && !game.disposed && token!==generation)changed({history});
            const row=history.analyses.find(item=>item.job===job.id && item.status==='ready');
            if(row){publish(token,{status:'ready',title,row,history,busy:true});return;}
            throw Error('prepareFailed');
          }
          await delay();
        }
      } catch(error) {failed(candidate);publish(token,{status:'error',error:error.message,busy:true});}
      finally {busy=false;if(!disposed)changed({busy:false,released:true});}
    }
  };
}
