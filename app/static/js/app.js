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
   details.textContent=`${result.reasons.join(' ')} Este porcentaje es un puntaje demostrativo, no una probabilidad real; no bloquea la operación.`;
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
 let stream=null, analysisTimer=null, lastAnalysis=null;
 const start=document.getElementById('startCamera'), stop=document.getElementById('stopCamera');
 const status=document.getElementById('cameraStatus'), placeholder=document.getElementById('cameraPlaceholder'), indicator=document.getElementById('cameraIndicator');
 const verified=document.getElementById('cameraVerified');
 const analysisField=document.getElementById('cameraAnalysis');
 const form=document.getElementById('operationForm');
 const formDuration=document.getElementById('formDurationSeconds');
 const submitButton=form?.querySelector('button[type="submit"]');
 const analysisBox=document.getElementById('cameraAnalysisBox');
 const analysisTitle=document.getElementById('cameraAnalysisTitle');
 const analysisDetails=document.getElementById('cameraAnalysisDetails');
 const facePosition=document.getElementById('facePosition');
 const faceVisibility=document.getElementById('faceVisibility');
 const formStartedAt=performance.now();
 if(submitButton) submitButton.disabled=true;
 const setStatus=(text)=>{if(status) status.textContent=text};
 const setVerified=(value)=>{if(verified)verified.value=value?'yes':'no';if(submitButton)submitButton.disabled=!value};
 const paintAnalysis=(result)=>{
  lastAnalysis=result;
  if(facePosition)facePosition.textContent=`${Math.round(result.position_score||0)}%`;
  if(faceVisibility)faceVisibility.textContent=`${Math.round(result.visibility_score||0)}%`;
  if(analysisBox)analysisBox.dataset.state=result.ready?'ok':'blocked';
  if(analysisTitle)analysisTitle.textContent=result.ready?'✓ Rostro verificado para la demo':'⚠ Verificación de cámara bloqueada';
  if(analysisDetails)analysisDetails.textContent=(result.indicators||[]).join(' ')||result.message||'';
  if(analysisField)analysisField.value=JSON.stringify({ready:result.ready,face_confidence:result.face_confidence,position_score:result.position_score,visibility_score:result.visibility_score,possible_obstruction:result.possible_obstruction});
  setVerified(Boolean(result.ready));
 };
 const analyzeCamera=async()=>{
  if(!stream?.active || video.readyState<2) return;
  const canvas=document.createElement('canvas');
  canvas.width=640; canvas.height=Math.round(640*(video.videoHeight/video.videoWidth||0.5625));
  const ctx=canvas.getContext('2d'); ctx.drawImage(video,0,0,canvas.width,canvas.height);
  const token=form?.querySelector('input[name="csrf_token"]')?.value || document.querySelector('#cameraCsrfForm input[name="csrf_token"]')?.value;
  const body=new URLSearchParams({frame:canvas.toDataURL('image/jpeg',0.72)});
  if(token)body.set('csrf_token',token);
  try{
   const response=await fetch('/camera/analyze',{method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded','X-CSRFToken':token||''},body});
   if(!response.headers.get('content-type')?.includes('application/json')){
    throw new Error(`El servidor respondió con un formato inesperado (HTTP ${response.status}).`);
   }
   const result=await response.json();
   if(!response.ok)throw new Error(result.error||'No se pudo analizar la cámara.');
   paintAnalysis(result);
  }catch(error){
   setVerified(false);
   if(analysisTitle)analysisTitle.textContent='⚠ No se pudo validar la cámara';
   if(analysisDetails)analysisDetails.textContent=error.message;
  }
 };
 const startAnalysis=()=>{clearInterval(analysisTimer);analyzeCamera();analysisTimer=setInterval(analyzeCamera,1200)};
 const stopCamera=()=>{
  clearInterval(analysisTimer);analysisTimer=null;
  if(stream){stream.getTracks().forEach(track=>track.stop());stream=null}
  video.srcObject=null;video.hidden=true;placeholder.hidden=false;indicator.hidden=true;
  start.disabled=false;stop.disabled=true;setVerified(false);setStatus('Cámara apagada. Actívala y verifica tu rostro para continuar.');
  if(analysisTitle)analysisTitle.textContent='Esperando análisis del rostro';
  if(analysisDetails)analysisDetails.textContent='Activa la cámara y colócate frente a ella.';
 };
 start.addEventListener('click',async()=>{
  if(!navigator.mediaDevices?.getUserMedia){setStatus('Este navegador no permite acceso a cámara. Usa localhost.');return}
  start.disabled=true;setStatus('Solicitando permiso del navegador…');
  try{
   stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'user',width:{ideal:640},height:{ideal:480}},audio:false});
   video.srcObject=stream;await video.play();video.hidden=false;placeholder.hidden=true;indicator.hidden=false;
   stop.disabled=false;setVerified(false);setStatus('Cámara activa. Analizando posición y visibilidad del rostro…');startAnalysis();
  }catch(error){
   if(stream){stream.getTracks().forEach(track=>track.stop());stream=null}
   start.disabled=false;setVerified(false);
   setStatus(error.name==='NotAllowedError'?'Permiso denegado. Habilita la cámara.':error.name==='NotFoundError'?'No se encontró una cámara conectada.':'No se pudo iniciar la cámara.');
  }
 });
 stop.addEventListener('click',stopCamera);
 if(form) form.addEventListener('submit',event=>{
  if(formDuration)formDuration.value=String(Math.max(0,Math.round((performance.now()-formStartedAt)/1000)));
  if(!stream || !stream.active || !lastAnalysis?.ready){event.preventDefault();setVerified(false);setStatus('La operación está bloqueada: el rostro debe estar visible, centrado y sin una obstrucción detectada.');return}
  if(submitButton){submitButton.disabled=true;submitButton.innerHTML='Procesando…'}
 });
 window.addEventListener('pagehide',stopCamera);
}
const toggle=document.getElementById('menuToggle');
if(toggle){const nav=document.getElementById('intranetNav');toggle.addEventListener('click',()=>{const isOpen=nav?.classList.toggle('open')??false;toggle.setAttribute('aria-expanded',String(isOpen));toggle.setAttribute('aria-label',isOpen?'Cerrar menú':'Abrir menú')});document.addEventListener('click',e=>{if(nav?.classList.contains('open')&&!nav.contains(e.target)&&!toggle.contains(e.target)){nav.classList.remove('open');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menú')}});nav?.querySelectorAll('a').forEach(link=>link.addEventListener('click',()=>{nav.classList.remove('open');toggle.setAttribute('aria-expanded','false');toggle.setAttribute('aria-label','Abrir menú')}))}
document.querySelectorAll('form').forEach(form=>{if(form.id==='operationForm')return;form.addEventListener('submit',()=>{const button=form.querySelector('button[type="submit"],button:not([type])');if(button){button.disabled=true;button.dataset.original=button.innerHTML;button.innerHTML='Procesando…'}})});
