/*
 * NORTE · movimiento y microinteracciones compartidas.
 * Sustituye a reveal.js. Todo es mejora progresiva: sin JS el contenido sigue visible.
 * Con prefers-reduced-motion se quitan los desplazamientos, pero la aparición se conserva como fundido de opacidad.
 */
(()=>{
 const reduced=window.matchMedia('(prefers-reduced-motion: reduce)').matches;
 const finePointer=window.matchMedia('(hover: hover) and (pointer: fine)').matches;
 const root=document.documentElement;

 /* 1. Aparición progresiva. Las clases home-motion-ready / home-is-visible se mantienen por compatibilidad. */
 const revealables=Array.from(document.querySelectorAll('[data-reveal]'));
 if(revealables.length){
  if(!('IntersectionObserver' in window)){
   revealables.forEach(el=>el.classList.add('home-is-visible'));
  }else{
   const groups=new Map();
   revealables.forEach(el=>{
    const parent=el.parentElement;
    const index=groups.get(parent)||0;
    groups.set(parent,index+1);
    el.style.setProperty('--reveal-delay',`${Math.min(index,5)*80}ms`);
   });
   const observer=new IntersectionObserver(entries=>{
    entries.forEach(entry=>{
     if(!entry.isIntersecting)return;
     entry.target.classList.add('home-is-visible');
     observer.unobserve(entry.target);
    });
   },{rootMargin:'0px 0px -6% 0px',threshold:.1});
   revealables.forEach(el=>observer.observe(el));
   root.classList.add('nds-motion','home-motion-ready');
  }
 }

 /* 2. Profundidad: el puntero mueve las capas de [data-depth-scope] (variables --px y --py entre -1 y 1). */
 if(!reduced&&finePointer){
  document.querySelectorAll('[data-depth-scope]').forEach(scope=>{
   let frame=0,px=0,py=0;
   const paint=()=>{frame=0;scope.style.setProperty('--px',px.toFixed(3));scope.style.setProperty('--py',py.toFixed(3))};
   const queue=()=>{if(!frame)frame=requestAnimationFrame(paint)};
   scope.addEventListener('pointermove',event=>{
    const box=scope.getBoundingClientRect();
    px=((event.clientX-box.left)/box.width-.5)*2;
    py=((event.clientY-box.top)/box.height-.5)*2;
    queue();
   },{passive:true});
   scope.addEventListener('pointerleave',()=>{px=0;py=0;queue()});
  });
 }

 /* 3. Resplandor que sigue al cursor en tarjetas con data-spotlight. */
 if(!reduced&&finePointer){
  let frame=0,target=null,x=0,y=0;
  document.addEventListener('pointermove',event=>{
   const card=event.target.closest?.('[data-spotlight]');
   if(!card)return;
   target=card;x=event.clientX;y=event.clientY;
   if(frame)return;
   frame=requestAnimationFrame(()=>{
    frame=0;
    const box=target.getBoundingClientRect();
    target.style.setProperty('--mx',`${x-box.left}px`);
    target.style.setProperty('--my',`${y-box.top}px`);
   });
  },{passive:true});
 }

 /* 4. Pestañas accesibles: [data-tabs] con role=tablist, role=tab y role=tabpanel. */
 document.querySelectorAll('[data-tabs]').forEach(group=>{
  const tabs=Array.from(group.querySelectorAll('[role=tab]'));
  const panels=tabs.map(tab=>document.getElementById(tab.getAttribute('aria-controls')));
  const select=(index,focus)=>{
   tabs.forEach((tab,i)=>{
    const active=i===index;
    tab.setAttribute('aria-selected',String(active));
    tab.tabIndex=active?0:-1;
    panels[i]?.classList.toggle('is-active',active);
    if(panels[i]){panels[i].toggleAttribute('inert',!active);panels[i].setAttribute('aria-hidden',String(!active))}
   });
   if(focus)tabs[index].focus();
  };
  tabs.forEach((tab,index)=>{
   tab.addEventListener('click',()=>select(index,false));
   tab.addEventListener('keydown',event=>{
    const last=tabs.length-1;
    const keys={ArrowDown:index===last?0:index+1,ArrowRight:index===last?0:index+1,ArrowUp:index===0?last:index-1,ArrowLeft:index===0?last:index-1,Home:0,End:last};
    if(event.key in keys){event.preventDefault();select(keys[event.key],true)}
   });
  });
  select(Math.max(0,tabs.findIndex(tab=>tab.getAttribute('aria-selected')==='true')),false);
 });

 /* 5. Estado de carga al enviar formularios: solo marca el botón, nunca lo desactiva ni retrasa el envío. */
 document.addEventListener('submit',event=>{
  const form=event.target;
  if(!(form instanceof HTMLFormElement)||form.method==='dialog'||event.defaultPrevented)return;
  const button=event.submitter||form.querySelector('button[type=submit].btn-primary,button[type=submit].nds-btn--primary');
  if(button&&button.matches('.btn-primary,.nds-btn--primary'))setTimeout(()=>button.setAttribute('aria-busy','true'),0);
 });
 window.addEventListener('pageshow',()=>document.querySelectorAll('[aria-busy=true]').forEach(el=>el.removeAttribute('aria-busy')));

 /* 6. Avisos: se pueden cerrar; los de éxito se retiran solos, los errores permanecen. */
 document.querySelectorAll('.flash').forEach(flash=>{
  flash.setAttribute('role',flash.classList.contains('error')?'alert':'status');
  const close=document.createElement('button');
  close.type='button';close.className='flash__close';close.setAttribute('aria-label','Cerrar aviso');
  close.innerHTML='<i class="bi bi-x-lg" aria-hidden="true"></i>';
  close.addEventListener('click',()=>{flash.classList.add('is-leaving');setTimeout(()=>flash.remove(),220)});
  flash.append(close);
  if(flash.classList.contains('success')&&!reduced)setTimeout(()=>close.click(),7000);
 });
})();
