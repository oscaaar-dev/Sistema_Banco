(()=>{
 document.querySelectorAll('[data-password-toggle]').forEach(button=>{
  button.addEventListener('click',()=>{
   const input=document.getElementById(button.dataset.passwordToggle);
   if(!input)return;
   const visible=input.type==='password';
   input.type=visible?'text':'password';
   button.setAttribute('aria-pressed',String(visible));
   button.setAttribute('aria-label',visible?'Ocultar contraseña':'Mostrar contraseña');
   const icon=button.querySelector('i');
   if(icon)icon.className=`bi ${visible?'bi-eye-slash':'bi-eye'}`;
   input.focus();
  });
 });
 const scenes=document.querySelectorAll('.auth-hero');
 if(!scenes.length||window.matchMedia('(prefers-reduced-motion: reduce)').matches)return;
 const layers=[...document.querySelectorAll('.auth-hero [data-parallax]')];
 let frame=0,targetX=0,targetY=0;
 const render=()=>{
  layers.forEach(layer=>{
   const depth=Number(layer.dataset.parallax)||0;
   layer.style.setProperty('--parallax-x',`${targetX*depth}px`);
   layer.style.setProperty('--parallax-y',`${targetY*depth}px`);
  });
  frame=0;
 };
 document.addEventListener('pointermove',event=>{
  if(event.pointerType==='touch')return;
  const scene=[...scenes].find(candidate=>{
   const bounds=candidate.getBoundingClientRect();
   return event.clientX>=bounds.left&&event.clientX<=bounds.right&&event.clientY>=bounds.top&&event.clientY<=bounds.bottom;
  });
  if(scene){
   const bounds=scene.getBoundingClientRect();
   targetX=(event.clientX-bounds.left-bounds.width/2)/12;
   targetY=(event.clientY-bounds.top-bounds.height/2)/12;
  }else targetX=targetY=0;
  if(!frame)frame=requestAnimationFrame(render);
 },{passive:true});
})();
