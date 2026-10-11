/*
 * NORTE · menú de la cabecera compartida (.sh).
 * Escritorio: enlaces visibles. Móvil / tablet (≤900px): el botón hamburguesa muestra u oculta el menú.
 * Esc y el clic fuera lo cierran; al elegir un enlace también se cierra.
 */
(()=>{
 const header=document.getElementById('siteHeader');
 const burger=document.getElementById('siteBurger');
 const nav=document.getElementById('siteNav');
 if(!header||!burger||!nav)return;

 const setOpen=open=>{
  header.classList.toggle('is-menu-open',open);
  burger.setAttribute('aria-expanded',String(open));
  burger.setAttribute('aria-label',open?'Cerrar menú principal':'Abrir menú principal');
  burger.querySelector('i').className=open?'bi bi-x-lg':'bi bi-list';
 };

 burger.addEventListener('click',()=>setOpen(!header.classList.contains('is-menu-open')));
 nav.addEventListener('click',event=>{if(event.target.closest('a'))setOpen(false)});
 document.addEventListener('keydown',event=>{
  if(event.key==='Escape'&&header.classList.contains('is-menu-open')){setOpen(false);burger.focus()}
 });
 document.addEventListener('pointerdown',event=>{
  if(!header.contains(event.target))setOpen(false);
 });
 window.matchMedia('(min-width: 901px)').addEventListener('change',()=>setOpen(false));
})();
