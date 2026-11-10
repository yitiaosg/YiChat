/** Menus keep one-finger scrolling but do not turn two fingers into page zoom. */
export function installMenuGestures(root:Document=document){
 let inMenu=false;
 const surface=(target:EventTarget|null)=>target instanceof Element&&!!target.closest('[data-no-zoom]');
 const touch=(event:TouchEvent)=>{
  if(event.type==='touchstart'&&surface(event.target))inMenu=true;
  if(event.touches.length>1&&(inMenu||surface(event.target))&&event.cancelable)event.preventDefault();
  if(event.type==='touchend'||event.type==='touchcancel'){if(!event.touches.length)inMenu=false}
 };
 const gesture=(event:Event)=>{if((inMenu||surface(event.target))&&event.cancelable)event.preventDefault()};
 const wheel=(event:WheelEvent)=>{if(event.ctrlKey&&surface(event.target)&&event.cancelable)event.preventDefault()};
 for(const type of ['touchstart','touchmove','touchend','touchcancel'])root.addEventListener(type,touch as EventListener,{passive:false});
 for(const type of ['gesturestart','gesturechange'])root.addEventListener(type,gesture,{passive:false});
 root.addEventListener('wheel',wheel,{passive:false});
 return()=>{
  for(const type of ['touchstart','touchmove','touchend','touchcancel'])root.removeEventListener(type,touch as EventListener);
  for(const type of ['gesturestart','gesturechange'])root.removeEventListener(type,gesture);
  root.removeEventListener('wheel',wheel);
 };
}
