/*
 * NORTE · mega-menú de la cabecera compartida (.sh).
 * Patrón de divulgación accesible: cada botón [aria-expanded] controla su panel.
 * - Escritorio: abre con hover (con pequeña demora), clic, Enter/Espacio y flecha abajo; cierra con Esc, clic fuera o al salir el foco.
 * - Móvil / tablet (≤1240px): el botón hamburguesa muestra el menú y cada sección funciona como acordeón.
 * Sin JavaScript los paneles siguen abriéndose con hover y foco (CSS).
 */
(()=>{
 const header=document.getElementById('siteHeader');
 if(!header)return;
 document.documentElement.classList.add('mm-js');

 const compact=window.matchMedia('(max-width: 1240px)');
 const canHover=window.matchMedia('(hover: hover) and (pointer: fine)');
 const items=Array.from(header.querySelectorAll('.mm-item'));
 const burger=document.getElementById('siteBurger');
 const nav=document.getElementById('siteNav');
 let closeTimer=0;

 const parts=item=>({trigger:item.querySelector(':scope > .mm-trigger'),panel:item.querySelector(':scope > .mm-panel')});

 // Los paneles cerrados no deben recibir foco ni aparecer para lectores de pantalla.
 const setOpen=(item,open)=>{
  const {trigger,panel}=parts(item);
  if(!trigger||!panel)return;
  item.classList.toggle('is-open',open);
  trigger.setAttribute('aria-expanded',String(open));
  panel.toggleAttribute('inert',!open&&!compact.matches);
 };
 const closeAll=(except)=>items.forEach(item=>{if(item!==except)setOpen(item,false)});
 const closeMenu=()=>{
  header.classList.remove('is-menu-open');
  if(burger){burger.setAttribute('aria-expanded','false');burger.setAttribute('aria-label','Abrir menú principal');burger.querySelector('i').className='bi bi-list'}
 };

 items.forEach(item=>{
  const {trigger,panel}=parts(item);
  if(!trigger||!panel)return;
  setOpen(item,false);

  trigger.addEventListener('click',()=>{
   window.clearTimeout(closeTimer);
   const open=!item.classList.contains('is-open');
   closeAll(open?item:undefined);
   setOpen(item,open);
  });

  trigger.addEventListener('keydown',event=>{
   if(event.key==='ArrowDown'){
    event.preventDefault();
    closeAll(item);setOpen(item,true);
    panel.querySelector('a,button')?.focus();
   }
   if(event.key==='ArrowRight'||event.key==='ArrowLeft'){
    const siblings=Array.from(item.parentElement.querySelectorAll(':scope > .mm-item > .mm-trigger'));
    const index=siblings.indexOf(trigger);
    const next=siblings[(index+(event.key==='ArrowRight'?1:siblings.length-1))%siblings.length];
    if(next&&!compact.matches){event.preventDefault();next.focus()}
   }
  });

  // Hover solo con puntero fino y en escritorio; en táctil manda el clic.
  item.addEventListener('pointerenter',event=>{
   if(compact.matches||!canHover.matches||event.pointerType==='touch')return;
   window.clearTimeout(closeTimer);
   closeAll(item);setOpen(item,true);
  });
  item.addEventListener('pointerleave',event=>{
   if(compact.matches||!canHover.matches||event.pointerType==='touch')return;
   window.clearTimeout(closeTimer);
   closeTimer=window.setTimeout(()=>setOpen(item,false),160);
  });

  // Si el foco sale del elemento, se cierra su panel.
  item.addEventListener('focusout',event=>{
   if(compact.matches)return;
   if(!item.contains(event.relatedTarget))setOpen(item,false);
  });
 });

 // Esc cierra el panel abierto (y devuelve el foco al botón) o el menú móvil.
 document.addEventListener('keydown',event=>{
  if(event.key!=='Escape')return;
  const open=items.find(item=>item.classList.contains('is-open'));
  if(open){
   const {trigger}=parts(open);
   const hadFocus=open.contains(document.activeElement);
   setOpen(open,false);
   if(hadFocus)trigger.focus();
   return;
  }
  if(header.classList.contains('is-menu-open')){closeMenu();burger?.focus()}
 });

 // Clic fuera de la cabecera.
 document.addEventListener('pointerdown',event=>{
  if(!header.contains(event.target)){closeAll();if(compact.matches)closeMenu()}
 });

 if(burger&&nav){
  burger.addEventListener('click',()=>{
   const open=!header.classList.contains('is-menu-open');
   header.classList.toggle('is-menu-open',open);
   burger.setAttribute('aria-expanded',String(open));
   burger.setAttribute('aria-label',open?'Cerrar menú principal':'Abrir menú principal');
   burger.querySelector('i').className=open?'bi bi-x-lg':'bi bi-list';
   if(!open)closeAll();
  });
 }

 // Al cambiar entre escritorio y compacto se limpia el estado para no dejar paneles a medias.
 compact.addEventListener('change',()=>{closeAll();closeMenu();items.forEach(item=>setOpen(item,false))});
})();
