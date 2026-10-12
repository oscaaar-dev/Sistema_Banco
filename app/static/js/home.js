const hero=document.querySelector('[data-hero-carousel]');
if(hero){
 const track=hero.querySelector('.home-hero-track');
 const slides=Array.from(track.querySelectorAll('[aria-roledescription="diapositiva"]'));
 const dots=Array.from(hero.parentElement.querySelectorAll('[data-hero-to]'));
 const status=hero.parentElement.querySelector('.home-hero-slide-status');
 const toggle=hero.parentElement.querySelector('[data-hero-toggle]');
 let activeIndex=0;
 let timer=null;
 let heroVisible=false;
 let focusInside=false;
 let paused=false;
 const stop=()=>{
  window.clearTimeout(timer);
  timer=null;
 };
 const show=(index)=>{
  activeIndex=(index+slides.length)%slides.length;
  track.style.transform=`translateX(-${activeIndex*100}%)`;
  slides.forEach((slide,position)=>{
   const active=position===activeIndex;
   slide.setAttribute('aria-hidden',String(!active));
   slide.inert=!active;
  });
  dots.forEach((dot,position)=>{
   if(position===activeIndex)dot.setAttribute('aria-current','true');
   else dot.removeAttribute('aria-current');
  });
  status.textContent=`0${activeIndex+1} / 0${slides.length}`;
 };
 const updateToggle=()=>{
  toggle.setAttribute('aria-pressed',String(paused));
  toggle.setAttribute('aria-label',paused?'Reanudar carrusel principal':'Pausar carrusel principal');
  toggle.querySelector('i').className=paused?'bi bi-play-fill':'bi bi-pause-fill';
 };
 const schedule=()=>{
  stop();
  if(paused||!heroVisible||focusInside||document.hidden)return;
  timer=window.setTimeout(()=>{show(activeIndex+1);schedule()},5200);
 };
 dots.forEach((dot)=>dot.addEventListener('click',()=>{show(Number(dot.dataset.heroTo));schedule()}));
 toggle.addEventListener('click',()=>{
  paused=!paused;
  if(!paused)focusInside=false;
  updateToggle();
  schedule();
 });
 hero.addEventListener('focusin',()=>{focusInside=true;stop()});
 hero.addEventListener('focusout',(event)=>{
  if(!hero.contains(event.relatedTarget)){focusInside=false;schedule()}
 });
 hero.addEventListener('keydown',(event)=>{
  if(event.key==='ArrowLeft'){event.preventDefault();show(activeIndex-1);schedule()}
  if(event.key==='ArrowRight'){event.preventDefault();show(activeIndex+1);schedule()}
 });
 document.addEventListener('visibilitychange',schedule);
 if('IntersectionObserver' in window){
  const observer=new IntersectionObserver((entries)=>{
   heroVisible=entries.some((entry)=>entry.isIntersecting);
   schedule();
  },{threshold:0.2});
  observer.observe(hero);
 }else{
  heroVisible=true;
 }
 show(0);
 updateToggle();
 schedule();
}

const parallaxItems=Array.from(document.querySelectorAll('.home-parallax[data-parallax]'));
const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
if(parallaxItems.length&&!reducedMotion.matches){
 let frame=0;
 const updateParallax=()=>{
  frame=0;
  const offset=window.scrollY;
  parallaxItems.forEach((item)=>{
   item.style.setProperty('--home-parallax-y',`${Math.round(offset*Number(item.dataset.parallax))}px`);
  });
 };
 const scheduleParallax=()=>{
  if(!frame)frame=window.requestAnimationFrame(updateParallax);
 };
 window.addEventListener('scroll',scheduleParallax,{passive:true});
 window.addEventListener('resize',scheduleParallax);
 updateParallax();
}

const cookieDialog=document.getElementById('cookieDialog');
const closeDialog=(button)=>button.closest('dialog')?.close();
document.querySelector('[data-open-cookies]')?.addEventListener('click',()=>{
 cookieDialog.showModal();
});
document.querySelectorAll('[data-close-dialog]').forEach((button)=>{
 button.addEventListener('click',()=>closeDialog(button));
});
document.querySelectorAll('dialog.home-overlay').forEach((dialog)=>{
 dialog.addEventListener('click',(event)=>{
  if(event.target===dialog)dialog.close();
 });
});
const cookiePreference=document.querySelector('[data-cookie-preferences]');
const cookieSave=document.querySelector('[data-save-cookies]');
const cookieAccept=document.querySelector('[data-accept-cookies]');
if(cookiePreference){
 const saved=window.localStorage.getItem('norte-cookie-preferences');
 cookiePreference.checked=saved==='enabled';
}
cookieSave?.addEventListener('click',()=>{
 window.localStorage.setItem('norte-cookie-preferences',cookiePreference.checked?'enabled':'disabled');
 window.localStorage.setItem('norte-cookie-choice','custom');
 cookieDialog.close();
});
cookieAccept?.addEventListener('click',()=>{
 if(cookiePreference)cookiePreference.checked=false;
 window.localStorage.setItem('norte-cookie-preferences','disabled');
 window.localStorage.setItem('norte-cookie-choice','necessary');
 cookieDialog.close();
});

if(!reducedMotion.matches&&!window.matchMedia('(hover: none)').matches){
 document.querySelectorAll('[data-parallax-card]').forEach((card)=>{
  card.addEventListener('mousemove',(event)=>{
   const bounds=card.getBoundingClientRect();
   const horizontal=(event.clientX-bounds.left)/bounds.width-.5;
   const vertical=(event.clientY-bounds.top)/bounds.height-.5;
   card.style.setProperty('--promo-rotate-y',`${(horizontal*7).toFixed(2)}deg`);
   card.style.setProperty('--promo-rotate-x',`${(-vertical*7).toFixed(2)}deg`);
  });
  card.addEventListener('pointerleave',()=>{
   card.style.removeProperty('--promo-rotate-y');
   card.style.removeProperty('--promo-rotate-x');
  });
 });
}
