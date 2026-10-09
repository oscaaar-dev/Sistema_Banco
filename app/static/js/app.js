const video=document.getElementById('cameraVideo');
const riskPreview=document.getElementById('riskPreview');
if(riskPreview){
 const amountInput=document.querySelector('#operationForm input[name="amount"]');
 const title=document.getElementById('riskTitle');
 const details=document.getElementById('riskDetails');
 let timer=null, controller=null;
 const updateRisk=async()=>{
  const amount=Number(amountInput?.value);
  if(!Number.isFinite(amount)||amount<=0){
   title.textContent='Indicador de riesgo';
   details.textContent='Ingresa un monto para estimar el nivel de alerta. No bloquea la operación.';
   return;
  }
  if(controller)controller.abort();
  controller=new AbortController();
  title.textContent='Calculando indicador…';
  details.textContent='Se evalúa el monto frente al saldo y al historial reciente.';
  try{
   const params=new URLSearchParams({kind:riskPreview.dataset.kind,amount:String(amount)});
   const response=await fetch(`${riskPreview.dataset.riskUrl}?${params}`,{signal:controller.signal});
   const result=await response.json();
   if(!response.ok)throw new Error(result.error||'No se pudo calcular el indicador.');
   title.textContent=`Nivel de alerta estimado: ${result.level} (${result.score}%)`;
   details.textContent=`${result.reasons.join(' ')} Este porcentaje es un indicador orientativo, no una probabilidad real; no bloquea la operación.`;
  }catch(error){
   if(error.name==='AbortError')return;
   title.textContent='Indicador no disponible';
   details.textContent=error.message;
  }
 };
 if(amountInput)amountInput.addEventListener('input',()=>{
  clearTimeout(timer);
  timer=setTimeout(updateRisk,250);
 });
}
if(video){
 let stream=null;
 const start=document.getElementById('startCamera'), stop=document.getElementById('stopCamera');
 const consent=document.getElementById('cameraConsent');
 const status=document.getElementById('cameraStatus'), placeholder=document.getElementById('cameraPlaceholder'), indicator=document.getElementById('cameraIndicator');
 const form=document.getElementById('operationForm');
 const submitButton=form?.querySelector('button[type="submit"]');
 const otpInput=form?.querySelector('input[name="otp_code"]');
 const formDuration=form?.querySelector('#formDurationSeconds');
 const formStartedAt=performance.now();
 const consentDialog=document.getElementById('operationConsentDialog');
 const acceptConsent=document.getElementById('acceptCameraConsent');
 const declineConsent=document.getElementById('declineCameraConsent');
 const consentVersion=consentDialog?.dataset.version;
 const consentError=document.getElementById('cameraConsentError');
 const csrfToken=form?.querySelector('input[name="csrf_token"]')?.value || document.querySelector('#cameraCsrfForm input[name="csrf_token"]')?.value || '';
 if(start)start.disabled=form?consent?.value!=='yes':!consent?.checked;
 const setStatus=(text)=>{if(status) status.textContent=text};
 const otpReady=()=>Boolean(otpInput&&/^\d{6}$/.test(otpInput.value));
 const updateSubmitState=()=>{
  if(submitButton)submitButton.disabled=!otpReady();
 };
 const stopCamera=()=>{
  if(stream){stream.getTracks().forEach(track=>track.stop());stream=null}
  video.srcObject=null;video.hidden=true;placeholder.hidden=false;indicator.hidden=true;
  start.disabled=Boolean(form&&consent?.value!=='yes');stop.disabled=true;
  setStatus('Cámara apagada. La operación se confirma con el código de correo.');
 };
 if(otpInput)otpInput.addEventListener('input',updateSubmitState);
 const startCamera=async()=>{
  if(form&&consent?.value!=='yes'){setStatus('Acepta el aviso para habilitar la vista previa local.');return}
  if(!form&&consent&&!consent.checked){setStatus('Acepta el aviso antes de activar la cámara.');return}
  if(!navigator.mediaDevices?.getUserMedia){setStatus('Este navegador no permite acceso a cámara. Continúa con el código de correo.');return}
  start.disabled=true;setStatus('Solicitando permiso de cámara al navegador…');
  try{
   stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:640},height:{ideal:480}},audio:false});
   video.srcObject=stream;await video.play();video.hidden=false;placeholder.hidden=true;indicator.hidden=false;
   stop.disabled=false;setStatus('Vista previa local activa. No se envían ni guardan fotogramas; confirma la operación con el código de correo.');
  }catch(error){
   if(stream){stream.getTracks().forEach(track=>track.stop());stream=null}
   start.disabled=Boolean(form&&consent?.value!=='yes');
   setStatus(error.name==='NotAllowedError'?'Permiso denegado. Puedes continuar con el código de correo.':error.name==='NotFoundError'?'No se encontró una cámara. Puedes continuar con el código de correo.':'No se pudo iniciar la cámara. Usa el código de correo.');
  }
 };
 start.addEventListener('click',async()=>{
  await startCamera();
 });
 if(acceptConsent&&consentDialog){
  acceptConsent.addEventListener('click',async()=>{
   acceptConsent.disabled=true;
   if(consentError)consentError.textContent='';
   try{
    const response=await fetch('/camera/consent',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken},body:JSON.stringify({version:consentVersion,kind:consentDialog.dataset.kind,idempotency_key:consentDialog.dataset.operationKey})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'No se pudo registrar el consentimiento.');
    if(consent)consent.value='yes';
    consentDialog.close('accepted');
    start.disabled=false;
    await startCamera();
   }catch(error){if(consentError)consentError.textContent=error.message;else setStatus(error.message)}
   finally{acceptConsent.disabled=false}
  });
 }
 declineConsent?.addEventListener('click',()=>{
  if(consent)consent.value='no';
  stopCamera();
  consentDialog?.close('declined');
  otpInput?.focus();
 });
 consentDialog?.addEventListener('cancel',()=>{if(consent)consent.value='no';stopCamera()});
 if(consent&&!form)consent.addEventListener('change',()=>{start.disabled=!consent.checked;if(!consent.checked)stopCamera()});
 stop.addEventListener('click',stopCamera);
 if(form) form.addEventListener('submit',event=>{
  if(formDuration)formDuration.value=String(Math.max(0,Math.round((performance.now()-formStartedAt)/1000)));
  if(!otpReady()){event.preventDefault();setStatus('Ingresa el código enviado a tu correo para confirmar la operación.');otpInput?.focus();return}
  if(submitButton){submitButton.disabled=true;submitButton.innerHTML='Procesando…'}
 });
 if(consentDialog&&typeof consentDialog.showModal==='function')consentDialog.showModal();
 window.addEventListener('pagehide',stopCamera);
}
const toggle=document.getElementById('menuToggle');
if(toggle){const nav=document.getElementById('intranetNav');toggle.addEventListener('click',()=>{const isOpen=nav?.classList.toggle('open')??false;toggle.setAttribute('aria-expanded',String(isOpen));toggle.setAttribute('aria-label',isOpen?'Cerrar menú':'Abrir menú')});document.addEventListener('click',e=>{if(nav?.classList.contains('open')&&!nav.contains(e.target)&&!toggle.contains(e.target)){nav.classList.remove('open');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menú')}});nav?.querySelectorAll('a').forEach(link=>link.addEventListener('click',()=>{nav.classList.remove('open');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menú')}))}
const operationForm=document.getElementById('operationForm');
const challengeForm=document.getElementById('transferCodeForm');
if(operationForm&&challengeForm){
 challengeForm.addEventListener('submit',()=>{
  ['beneficiary_id','amount','idempotency_key','service','phone'].forEach(name=>{
   const source=operationForm.querySelector(`[name="${name}"]`);
   const target=challengeForm.querySelector(`[name="${name}"]`);
   if(source&&target)target.value=source.value;
  });
 });
}
document.querySelectorAll('form').forEach(form=>{if(form.id==='operationForm')return;form.addEventListener('submit',()=>{const button=form.querySelector('button[type="submit"],button:not([type])');if(button){button.disabled=true;button.dataset.original=button.innerHTML;button.innerHTML='Procesando…'}})});
