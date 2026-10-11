/*
 * NORTE · requisitos de contraseña en vivo (registro y recuperación).
 * Solo mejora la experiencia: el servidor vuelve a validar todo y es quien autoriza.
 * - Los patrones se leen del HTML (data-pattern), generado por el servidor desde la política única.
 * - Nunca se recorta ni se modifica lo que escribe la persona (sin trim, sin maxlength).
 * - Antes de escribir no se muestra ningún error; al escribir, lo cumplido se marca y lo pendiente queda neutro;
 *   lo incumplido se resalta al salir del campo o al intentar enviar.
 */
(()=>{
 const ICONS={idle:'bi-circle',ok:'bi-check-circle-fill',bad:'bi-x-circle-fill'};

 document.querySelectorAll('form').forEach(form=>{
  const source=form.querySelector('[data-pw-source]');
  const rulesList=form.querySelector('[data-pw-rules]');
  if(!source||!rulesList)return;
  const confirm=form.querySelector('[data-pw-confirm]');
  const matchNote=form.querySelector('[data-pw-match]');
  const summary=form.querySelector('[data-pw-summary]');
  const min=Number(rulesList.dataset.min),max=Number(rulesList.dataset.max);
  const items=Array.from(rulesList.querySelectorAll('li[data-rule]'));
  let touched=false,confirmTouched=false;

  const setIcon=(item,state)=>{
   const icon=item.querySelector('i');
   if(icon)icon.className=`bi ${ICONS[state]}`;
   item.classList.toggle('is-ok',state==='ok');
   item.classList.toggle('is-bad',state==='bad');
  };
  const evaluate=value=>items.map(item=>{
   const key=item.dataset.rule;
   const ok=key==='length'?(value.length>=min&&value.length<=max):new RegExp(item.dataset.pattern).test(value);
   return {item,key,ok};
  });

  const paint=()=>{
   const value=source.value;
   const results=evaluate(value);
   const typed=value.length>0;
   results.forEach(({item,key,ok})=>{
    if(!typed&&!touched){setIcon(item,'idle');return}
    if(ok){setIcon(item,'ok');return}
    // Pasarse de longitud ya no se arregla escribiendo más: se marca de inmediato.
    const tooLong=key==='length'&&value.length>max;
    setIcon(item,(tooLong||touched)?'bad':'idle');
   });
   const met=results.filter(r=>r.ok).length;
   if(summary&&typed)summary.textContent=`Cumples ${met} de ${results.length} requisitos.`;
   source.setCustomValidity(met===results.length?'':'La contraseña debe cumplir todos los requisitos de la lista.');
   source.setAttribute('aria-invalid',String(typed&&touched&&met!==results.length));
   paintMatch();
   return met===results.length;
  };

  const paintMatch=()=>{
   if(!confirm||!matchNote)return;
   const show=confirmTouched&&confirm.value.length>0;
   const same=confirm.value===source.value;
   confirm.setCustomValidity(same?'':'Las contraseñas no coinciden.');
   matchNote.hidden=!show;
   if(!show)return;
   matchNote.className=`pw-match ${same?'is-ok':'is-bad'}`;
   matchNote.textContent=same?'Las contraseñas coinciden.':'Las contraseñas no coinciden.';
   confirm.setAttribute('aria-invalid',String(!same));
  };

  source.addEventListener('input',paint);
  // Si el navegador bloquea el envío por su validación nativa, también se resaltan los pendientes.
  source.addEventListener('invalid',()=>{if(source.value.length>0){touched=true;paint()}});
  source.addEventListener('blur',()=>{if(source.value.length>0){touched=true;paint()}});
  if(confirm){
   confirm.addEventListener('input',()=>{confirmTouched=true;paintMatch()});
   confirm.addEventListener('blur',()=>{if(confirm.value.length>0){confirmTouched=true;paintMatch()}});
  }

  // Un solo envío por operación; si el navegador bloquea por validación, se muestran los pendientes.
  form.addEventListener('submit',event=>{
   touched=true;confirmTouched=true;
   const valid=paint();
   if(!valid||(confirm&&confirm.value!==source.value)){
    event.preventDefault();
    (valid?confirm:source).focus();
    return;
   }
   if(form.dataset.submitting==='true'){event.preventDefault();return}
   form.dataset.submitting='true';
  });
  window.addEventListener('pageshow',()=>{form.dataset.submitting='false'});
  paint();
 });
})();
