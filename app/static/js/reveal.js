const revealElements=Array.from(document.querySelectorAll('[data-reveal]'));
if(revealElements.length){
 document.documentElement.classList.add('home-motion-ready');
 revealElements.forEach((element,index)=>{
  element.style.setProperty('--home-reveal-delay',`${Math.min(index%4,3)*90}ms`);
 });
 let revealFrame=0;
 const updateRevealVisibility=()=>{
  revealFrame=0;
  const viewportHeight=document.documentElement.clientHeight;
  revealElements.forEach((element)=>{
   const bounds=element.getBoundingClientRect();
   const isVisible=bounds.bottom>viewportHeight*.1&&bounds.top<viewportHeight*.9;
   element.classList.toggle('home-is-visible',isVisible);
  });
 };
 const scheduleRevealUpdate=()=>{
  if(!revealFrame)revealFrame=window.requestAnimationFrame(updateRevealVisibility);
 };
 window.addEventListener('scroll',scheduleRevealUpdate,{passive:true});
 window.addEventListener('resize',scheduleRevealUpdate);
 document.addEventListener('visibilitychange',scheduleRevealUpdate);
 updateRevealVisibility();
}
