// A single continuous session; retries never create overlapping preparation jobs.
export function createAutomatic({advance, report, schedule=setTimeout, cancel=clearTimeout}) {
  let enabled=false, busy=false, pending=false, timer=null, generation=0, failures=0;
  async function step(token) {
    if(!enabled || token!==generation || busy)return;
    busy=true;pending=false;
    try {
      const played=await advance(()=>enabled && token===generation);
      if(!enabled || token!==generation)return;
      failures=played?0:failures+1;
      if(!played)pending=true;
    } catch(error) {
      if(!enabled || token!==generation)return;
      report(error);
      if(error.name==='NotAllowedError') {enabled=false;return;}
      failures++;pending=true;
    } finally {
      busy=false;
      if(enabled && pending)arm(failures?Math.min(60000,2000*2**Math.min(failures,5)):0);
    }
  }
  function arm(delay=0) {
    if(!enabled)return;
    pending=true;
    if(busy)return;
    cancel(timer);const token=generation;
    timer=schedule(()=>{timer=null;void step(token);},delay);
  }
  return {
    get enabled(){return enabled;},
    start(){if(enabled)return;enabled=true;generation++;failures=0;arm();},
    next(){arm();},
    stop(){enabled=false;pending=false;generation++;cancel(timer);timer=null;},
  };
}
