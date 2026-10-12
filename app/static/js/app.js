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
 /*
  * Cámara obligatoria. Flujo: aviso (sin opción de rechazar) → permiso del navegador → vista previa →
  * al confirmar se toma UNA foto (JPEG, máximo 640 px de ancho), se adjunta al formulario y la cámara se apaga.
  * Sin cámara activa el botón de confirmar permanece bloqueado: no hay alternativa.
  */
 const MAX_WIDTH=640,MAX_BYTES=300*1024;
 let stream=null,submitting=false;
 const form=document.querySelector('form[data-camera-form]');
 const start=document.getElementById('startCamera'), stop=document.getElementById('stopCamera');
 const consent=document.getElementById('cameraConsent');
 const status=document.getElementById('cameraStatus'), placeholder=document.getElementById('cameraPlaceholder'), indicator=document.getElementById('cameraIndicator');
 const help=document.getElementById('cameraHelp');
 const captureInput=document.getElementById('captureInput');
 const submitButton=form?.querySelector('button[type="submit"]');
 const otpInput=form?.querySelector('input[name="otp_code"]');
 const formDuration=form?.querySelector('input[name="form_duration_seconds"]');
 const formStartedAt=performance.now();
 const consentDialog=document.getElementById('operationConsentDialog');
 const acceptConsent=document.getElementById('acceptCameraConsent');
 const consentError=document.getElementById('cameraConsentError');
 const csrfToken=form?.querySelector('input[name="csrf_token"]')?.value||document.querySelector('meta[name="csrf-token"]')?.content||'';
 const setStatus=text=>{if(status)status.textContent=text};
 const showHelp=show=>{if(help)help.hidden=!show};
 const hasConsent=()=>consent?.value==='yes';
 const otpReady=()=>Boolean(otpInput&&/^\d{6}$/.test(otpInput.value));
 const cameraOn=()=>Boolean(stream&&stream.active&&video.videoWidth>0);
 /* El botón de confirmar solo se habilita con la cámara encendida y el código completo. */
 const updateSubmitState=()=>{if(submitButton&&!submitting)submitButton.disabled=!(cameraOn()&&otpReady())};
 const stopCamera=(message)=>{
  if(stream){stream.getTracks().forEach(track=>track.stop());stream=null}
  video.srcObject=null;video.hidden=true;placeholder.hidden=false;indicator.hidden=true;
  if(start)start.disabled=false;if(stop)stop.disabled=true;
  if(message)setStatus(message);
  updateSubmitState();
 };
 const openNotice=()=>{
  if(consentDialog&&typeof consentDialog.showModal==='function'&&!consentDialog.open)consentDialog.showModal();
 };
 const startCamera=async()=>{
  if(!hasConsent()){openNotice();return}
  showHelp(false);
  if(!navigator.mediaDevices?.getUserMedia){
   showHelp(true);setStatus('Este navegador no permite usar la cámara. Sin cámara no se puede confirmar la operación.');return;
  }
  if(start)start.disabled=true;setStatus('Solicitando permiso de cámara al navegador…');
  try{
   stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:640},height:{ideal:480}},audio:false});
   video.srcObject=stream;await video.play();
   video.hidden=false;placeholder.hidden=true;indicator.hidden=false;
   if(stop)stop.disabled=false;
   setStatus(otpReady()?'Cámara activa. Pulsa «Revisar y confirmar» para tomar la foto y completar la operación.':'Cámara activa. Escribe el código de tu correo para poder confirmar.');
   stream.getVideoTracks()[0]?.addEventListener('ended',()=>stopCamera('La cámara se desconectó. Actívala de nuevo para confirmar.'));
   updateSubmitState();
  }catch(error){
   if(stream){stream.getTracks().forEach(track=>track.stop());stream=null}
   if(start)start.disabled=false;
   showHelp(true);
   const blocked=error&&(error.name==='NotAllowedError'||error.name==='SecurityError');
   const missing=error&&(error.name==='NotFoundError'||error.name==='OverconstrainedError');
   setStatus(blocked?'El navegador bloqueó la cámara. Sin cámara no se puede confirmar la operación: sigue los pasos de abajo para permitirla.'
    :missing?'No encontramos ninguna cámara conectada. Sin cámara no se puede confirmar la operación.'
    :'No se pudo iniciar la cámara (puede estar en uso por otra aplicación). Sin cámara no se puede confirmar la operación.');
   updateSubmitState();
  }
 };
 /* Toma una sola foto del video: canvas → JPEG, máximo 640 px de ancho, calidad ~0.7 (baja si pasa de 300 KB). */
 const takePhoto=()=>new Promise((resolve,reject)=>{
  const width=Math.min(MAX_WIDTH,video.videoWidth),height=Math.round(video.videoHeight*width/video.videoWidth);
  const canvas=document.createElement('canvas');canvas.width=width;canvas.height=height;
  canvas.getContext('2d').drawImage(video,0,0,width,height);
  const encode=quality=>canvas.toBlob(blob=>{
   if(!blob)return reject(new Error('No se pudo generar la foto.'));
   if(blob.size>MAX_BYTES&&quality>0.35)return encode(quality-0.2);
   if(blob.size>MAX_BYTES)return reject(new Error('La foto es demasiado pesada.'));
   resolve(blob);
  },'image/jpeg',quality);
  encode(0.7);
 });
 start?.addEventListener('click',startCamera);
 stop?.addEventListener('click',()=>stopCamera('Cámara apagada. Actívala de nuevo para poder confirmar la operación.'));
 otpInput?.addEventListener('input',()=>{
  updateSubmitState();
  if(cameraOn()&&otpReady())setStatus('Cámara activa. Pulsa «Revisar y confirmar» para tomar la foto y completar la operación.');
 });
 if(acceptConsent&&consentDialog){
  acceptConsent.addEventListener('click',async()=>{
   acceptConsent.disabled=true;
   if(consentError)consentError.textContent='';
   try{
    const response=await fetch('/camera/consent',{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':csrfToken},body:JSON.stringify({version:consentDialog.dataset.version,kind:consentDialog.dataset.kind,idempotency_key:consentDialog.dataset.operationKey})});
    const result=await response.json();
    if(!response.ok)throw new Error(result.error||'No se pudo registrar tu aceptación.');
    if(consent)consent.value='yes';
    consentDialog.close('accepted');
    await startCamera();
   }catch(error){if(consentError)consentError.textContent=error.message}
   finally{acceptConsent.disabled=false}
  });
  /* Esc no cierra el aviso: o se acepta y se activa la cámara, o se cancela la operación. */
  consentDialog.addEventListener('cancel',event=>event.preventDefault());
  if(consentDialog.dataset.acknowledged==='yes'&&consent)consent.value='yes';
  else openNotice();
 }
 form?.addEventListener('submit',async event=>{
  if(submitting)return;
  event.preventDefault();
  if(formDuration)formDuration.value=String(Math.max(0,Math.round((performance.now()-formStartedAt)/1000)));
  if(!hasConsent()){openNotice();return}
  if(!otpReady()){setStatus('Escribe el código de 6 dígitos que enviamos a tu correo.');otpInput?.focus();return}
  if(!cameraOn()){setStatus('Activa la cámara para confirmar: sin cámara no se puede completar la operación.');showHelp(true);return}
  submitting=true;
  if(submitButton){submitButton.disabled=true;submitButton.innerHTML='Tomando foto…'}
  try{
   const blob=await takePhoto();
   const transfer=new DataTransfer();
   transfer.items.add(new File([blob],'capture.jpg',{type:'image/jpeg'}));
   captureInput.files=transfer.files;
  }catch(error){
   submitting=false;
   setStatus('No se pudo tomar la foto de seguridad. Vuelve a activar la cámara e inténtalo otra vez.');
   if(submitButton){submitButton.innerHTML='Revisar y confirmar <i class="bi bi-arrow-right"></i>'}
   updateSubmitState();
   return;
  }
  stopCamera();  /* la cámara se apaga apenas se toma la foto */
  /* requestSubmit conserva el valor del botón (el traslado interno usa name="action" value="confirm"). */
  form.requestSubmit(submitButton);
  if(submitButton){submitButton.disabled=true;submitButton.innerHTML='Procesando…'}
 });
 updateSubmitState();
 window.addEventListener('pagehide',()=>stopCamera());
}
const toggle=document.getElementById('menuToggle');
if(toggle){const nav=document.getElementById('intranetNav');toggle.addEventListener('click',()=>{const isOpen=nav?.classList.toggle('open')??false;toggle.setAttribute('aria-expanded',String(isOpen));toggle.setAttribute('aria-label',isOpen?'Cerrar menú':'Abrir menú')});document.addEventListener('click',e=>{if(nav?.classList.contains('open')&&!nav.contains(e.target)&&!toggle.contains(e.target)){nav.classList.remove('open');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menú')}});nav?.querySelectorAll('a').forEach(link=>link.addEventListener('click',()=>{nav.classList.remove('open');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menú')}))}
const operationForm=document.getElementById('operationForm');
const challengeForm=document.getElementById('transferCodeForm');
if(operationForm&&challengeForm){
 /* "Enviar código" solo se habilita con el destino (contacto, servicio o celular) y un monto válido. */
 const codeButton=challengeForm.querySelector('button[type="submit"]');
 const codeHint=document.getElementById('transferCodeHint');
 const target=['beneficiary_id','service','phone'].map(name=>operationForm.querySelector(`[name="${name}"]`)).find(Boolean);
 const amountField=operationForm.querySelector('[name="amount"]');
 const codeReady=()=>{
  const amount=(amountField?.value||'').trim().replace(',','.');
  return Boolean(target&&target.value.trim()&&/^\d+(\.\d{1,2})?$/.test(amount)&&Number(amount)>=0.01&&Number(amount)<=10000);
 };
 const refreshCodeButton=()=>{
  const ready=codeReady();
  if(codeButton)codeButton.disabled=!ready;
  if(codeHint)codeHint.hidden=ready;
 };
 operationForm.addEventListener('input',refreshCodeButton);
 operationForm.addEventListener('change',refreshCodeButton);
 refreshCodeButton();
 challengeForm.addEventListener('submit',event=>{
  if(!codeReady()){event.preventDefault();refreshCodeButton();return}
  ['beneficiary_id','amount','idempotency_key','service','phone'].forEach(name=>{
   const source=operationForm.querySelector(`[name="${name}"]`);
   const field=challengeForm.querySelector(`[name="${name}"]`);
   if(source&&field)field.value=source.value;
  });
 });
}
document.querySelectorAll('form').forEach(form=>{if(form.hasAttribute('data-camera-form'))return;form.addEventListener('submit',()=>{const button=form.querySelector('button[type="submit"],button:not([type])');if(button){button.disabled=true;button.dataset.original=button.innerHTML;button.innerHTML='Procesando…'}})});
