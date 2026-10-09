const carousel=document.querySelector('[data-home-carousel]');
if(carousel){
 const track=carousel.querySelector('.home-carousel-track');
 const slides=Array.from(track.querySelectorAll('[aria-roledescription="diapositiva"]'));
 const pagination=carousel.querySelector('.home-carousel-pagination');
 const status=carousel.querySelector('.home-carousel-status');
 const viewport=carousel.querySelector('.home-carousel-viewport');
 const previous=carousel.querySelector('[data-carousel-previous]');
 const next=carousel.querySelector('[data-carousel-next]');
 const toggle=carousel.querySelector('[data-carousel-toggle]');
 const reducedMotion=window.matchMedia('(prefers-reduced-motion: reduce)');
 const autoplayDelay=4000;
 let activeIndex=0;
 let visibleCount=3;
 let pointerStart=null;
 let autoplayTimer=null;
 let focusInside=false;
 let carouselVisible=false;
 let manuallyPaused=false;
 const updateVisibleCount=()=>{
  visibleCount=window.matchMedia('(max-width: 700px)').matches?1:window.matchMedia('(max-width: 950px)').matches?2:3;
  activeIndex=Math.min(activeIndex,slides.length-visibleCount);
  renderPagination();
  showSlide(activeIndex);
 };
 const renderPagination=()=>{
  const pageCount=slides.length-visibleCount+1;
  pagination.replaceChildren();
  for(let index=0;index<pageCount;index++){
   const button=document.createElement('button');
   button.type='button';
   button.dataset.carouselTo=String(index);
   button.setAttribute('aria-label',`Mostrar grupo ${index+1}`);
   button.addEventListener('click',()=>showSlide(index));
   pagination.append(button);
  }
 };
 const showSlide=(index)=>{
  const maxIndex=slides.length-visibleCount;
  activeIndex=(index+maxIndex+1)%(maxIndex+1);
  const gap=parseFloat(getComputedStyle(track).gap)||0;
  const step=slides[0].getBoundingClientRect().width+gap;
  track.style.transform=`translateX(-${activeIndex*step}px)`;
  slides.forEach((slide,position)=>{
   const active=position>=activeIndex&&position<activeIndex+visibleCount;
   slide.setAttribute('aria-hidden',String(!active));
   slide.inert=!active;
  });
  pagination.querySelectorAll('[data-carousel-to]').forEach((indicator)=>{
   if(Number(indicator.dataset.carouselTo)===activeIndex)indicator.setAttribute('aria-current','true');
   else indicator.removeAttribute('aria-current');
  });
  const lastVisible=Math.min(activeIndex+visibleCount,slides.length);
  status.textContent=`${activeIndex+1}–${lastVisible} / ${slides.length}`;
 };
 const updateAutoplayButton=()=>{
  const icon=toggle.querySelector('i');
  toggle.setAttribute('aria-pressed',String(manuallyPaused));
  if(manuallyPaused){
   toggle.setAttribute('aria-label','Reanudar carrusel automático');
   icon.className='bi bi-play-fill';
  }else{
   toggle.setAttribute('aria-label','Pausar carrusel');
   icon.className='bi bi-pause-fill';
  }
 };
 const stopAutoplay=()=>{
  window.clearTimeout(autoplayTimer);
  autoplayTimer=null;
 };
 const startAutoplay=()=>{
  stopAutoplay();
  if(manuallyPaused||focusInside||!carouselVisible||document.hidden||slides.length<=visibleCount)return;
  autoplayTimer=window.setTimeout(()=>{
   showSlide(activeIndex+1);
   startAutoplay();
  },autoplayDelay);
 };
 previous.addEventListener('click',()=>{showSlide(activeIndex-1);startAutoplay()});
 next.addEventListener('click',()=>{showSlide(activeIndex+1);startAutoplay()});
 pagination.addEventListener('click',()=>startAutoplay());
 toggle.addEventListener('click',()=>{
  manuallyPaused=!manuallyPaused;
  if(!manuallyPaused)focusInside=false;
  updateAutoplayButton();
  startAutoplay();
 });
 carousel.addEventListener('pointerup',()=>{
  focusInside=false;
  startAutoplay();
 });
 carousel.addEventListener('focusin',()=>{focusInside=true;stopAutoplay()});
 carousel.addEventListener('focusout',(event)=>{
  if(!carousel.contains(event.relatedTarget)){focusInside=false;startAutoplay()}
 });
 document.addEventListener('visibilitychange',startAutoplay);
 carousel.addEventListener('keydown',(event)=>{
  if(event.key==='ArrowLeft'){event.preventDefault();showSlide(activeIndex-1);startAutoplay()}
  if(event.key==='ArrowRight'){event.preventDefault();showSlide(activeIndex+1);startAutoplay()}
 });
 viewport.addEventListener('pointerdown',(event)=>{
  if(event.pointerType==='mouse'&&event.button!==0)return;
  pointerStart={x:event.clientX,y:event.clientY};
 });
 viewport.addEventListener('pointerup',(event)=>{
  if(!pointerStart)return;
  const deltaX=event.clientX-pointerStart.x;
  const deltaY=event.clientY-pointerStart.y;
  pointerStart=null;
  if(Math.abs(deltaX)>45&&Math.abs(deltaX)>Math.abs(deltaY)){showSlide(activeIndex+(deltaX<0?1:-1));startAutoplay()}
 });
 viewport.addEventListener('pointercancel',()=>{pointerStart=null});
 window.addEventListener('resize',updateVisibleCount);
 updateVisibleCount();
 updateAutoplayButton();
 if('IntersectionObserver' in window){
  const carouselObserver=new IntersectionObserver((entries)=>{
   carouselVisible=entries.some((entry)=>entry.isIntersecting);
   startAutoplay();
  },{threshold:0.15});
  carouselObserver.observe(carousel);
 }else{
  carouselVisible=true;
  startAutoplay();
 }
}

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
const helpDialog=document.getElementById('helpDialog');
const closeDialog=(button)=>button.closest('dialog')?.close();
document.querySelector('[data-open-cookies]')?.addEventListener('click',()=>{
 cookieDialog.showModal();
});
document.querySelector('[data-open-help]')?.addEventListener('click',()=>{
 helpDialog.showModal();
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
