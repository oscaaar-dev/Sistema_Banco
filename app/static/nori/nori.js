/* Nori · chatbot de Norte — máquina de poses + chat.
   Sin dependencias ni servicios externos. Todo el movimiento vive en nori.css; aquí solo se cambian
   clases y se llama al backend de Norte (POST /api/nori/chat, con token CSRF). */
(function () {
  'use strict';
  var root = document.getElementById('nori');
  if (!root) return;
  document.documentElement.classList.add('has-nori');   // reserva espacio al final de la página (ver nori.css)

  var IMG = root.dataset.img;
  var ENDPOINT = root.dataset.endpoint;
  var CSRF_URL = root.dataset.csrfUrl;
  var MAX_CHARS = 500;
  var motion = matchMedia('(prefers-reduced-motion: reduce)');
  var reduce = function () { return motion.matches; };

  /* ---------- poses disponibles (frames + efectos) ---------- */
  var POSES = {
    reposo:      { frames: [{ b: 'reposo' }] },
    hola:        { frames: [{ b: 'hola', fx: 'hola' }, { b: 'hola-2', fx: 'hola-2' }] },
    seguridad:   { frames: [{ b: 'seguridad' }] },
    exito:       { frames: [{ b: 'exito' }], fx: 'exito' },
    pensando:    { frames: [{ b: 'pensando' }], fx: 'pensando' },
    escribiendo: { frames: [{ b: 'escribiendo' }, { b: 'escribiendo-2' }] },
    dormido:     { frames: [{ b: 'dormido' }], fx: 'dormido' },
    confundido:  { frames: [{ b: 'confundido' }], fx: 'confundido' },
    hablando:    { frames: [{ b: 'hablando' }, { b: 'hablando-2' }] }
  };
  var MOODS = { hola: 1, seguridad: 1, exito: 1, confundido: 1, reposo: 1 };
  var QUICK = ['¿Cómo ingreso?', '¿Es seguro?', 'Abrir una cuenta', '¿Qué es Norte?'];
  var OFFLINE = 'No pude conectarme ahora mismo. Puedes intentarlo de nuevo en un momento o revisar las páginas de Norte.';

  function mkImg(file) {
    var i = new Image();
    i.src = IMG + file + '.png';
    i.alt = ''; i.decoding = 'async'; i.draggable = false;
    return i;
  }
  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  /* Solo se aceptan enlaces internos del propio sitio. */
  function safeHref(href) {
    return typeof href === 'string' && /^\/(?![\/\\])/.test(href) ? href : null;
  }

  /* ---------- construir mascota y panel ---------- */
  var mascot = el('button', 'nori__mascot');
  mascot.type = 'button';
  mascot.setAttribute('aria-label', 'Abrir chat con Nori');
  mascot.setAttribute('aria-expanded', 'false');
  mascot.setAttribute('aria-controls', 'nori-panel');
  var tip = el('span', 'nori__tip', '¡Hola! Soy Nori. ¿Te ayudo?');
  var stage = el('span', 'nori__stage');
  stage.setAttribute('aria-hidden', 'true');
  var swap = el('span', 'nori__swap');
  var poses = el('span', 'nori__poses');
  var els = {};
  Object.keys(POSES).forEach(function (name) {
    var def = POSES[name];
    var p = el('span', 'nori-pose nori-pose--' + name);
    def.frames.forEach(function (f, i) {
      var fr = el('span', 'nori-fr nori-fr' + (i + 1));
      fr.appendChild(mkImg('nori-' + f.b));
      if (f.fx) { var fi = mkImg('fx-' + f.fx); fi.className = 'nori-fx-in'; fr.appendChild(fi); }
      p.appendChild(fr);
    });
    if (def.fx) { var fx = mkImg('fx-' + def.fx); fx.className = 'nori-fx'; p.appendChild(fx); }
    poses.appendChild(p);
    els[name] = p;
  });
  ['l', 'r'].forEach(function (side) {
    var eye = el('span', 'nori-eye nori-eye--' + side);
    eye.appendChild(el('i', 'nori-lid nori-lid--t'));
    eye.appendChild(el('i', 'nori-lid nori-lid--b'));
    eye.appendChild(el('i', 'nori-line'));
    poses.appendChild(eye);
  });
  swap.appendChild(poses); stage.appendChild(swap);
  mascot.appendChild(tip);
  mascot.appendChild(el('span', 'nori__shadow'));
  mascot.appendChild(stage);

  var panel = el('section', 'nori__panel');
  panel.id = 'nori-panel'; panel.hidden = true;
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Chat con Nori');
  var head = el('header', 'nori__head');
  var headTxt = el('div', 'nori__head-txt');
  headTxt.appendChild(el('p', 'nori__title', 'Nori'));
  headTxt.appendChild(el('p', 'nori__sub', 'Asistente de Norte · demo académica'));
  var close = el('button', 'nori__close', '×');
  close.type = 'button'; close.setAttribute('aria-label', 'Cerrar chat');
  head.appendChild(headTxt); head.appendChild(close);
  var log = el('div', 'nori__log');
  log.setAttribute('role', 'log'); log.setAttribute('aria-live', 'polite');
  var form = el('form', 'nori__form');
  form.setAttribute('autocomplete', 'off');
  var input = el('input', 'nori__input');
  input.type = 'text'; input.maxLength = MAX_CHARS; input.placeholder = 'Escribe tu consulta…';
  input.setAttribute('aria-label', 'Tu mensaje para Nori');
  var send = el('button', 'nori__send', 'Enviar');
  send.type = 'submit';
  form.appendChild(input); form.appendChild(send);
  var legal = el('p', 'nori__legal', 'Demo académica. No escribas DNI, contraseñas, tarjetas ni códigos.');
  [head, log, form, legal].forEach(function (n) { panel.appendChild(n); });
  root.appendChild(panel); root.appendChild(mascot);

  /* ---------- máquina de poses ---------- */
  var pose = null, token = 0;
  function setPose(name) {
    if (!els[name] || pose === name) return;
    if (pose) els[pose].classList.remove('is-on');
    els[name].classList.add('is-on');
    pose = name;
    stage.dataset.pose = name;
    if (!reduce()) { swap.classList.remove('is-swap'); void swap.offsetWidth; swap.classList.add('is-swap'); }
  }
  swap.addEventListener('animationend', function (e) { if (e.target === swap) swap.classList.remove('is-swap'); });
  /* Muestra una pose y, pasado `ms`, vuelve a reposo (si nadie cambió la pose mientras tanto). */
  function act(name, ms) {
    var t = ++token;
    setPose(name);
    if (ms) setTimeout(function () { if (t === token && !busy) setPose('reposo'); }, ms);
  }

  /* ---------- sueño por inactividad ---------- */
  var busy = false, sleeping = false, isOpen = false, idleT;
  function sleep() {
    if (busy || isOpen) return bump();
    sleeping = true; ++token; setPose('dormido');
  }
  function bump() {
    clearTimeout(idleT);
    if (sleeping) { sleeping = false; if (!busy) act('hola', 1700); }
    idleT = setTimeout(sleep, 45000);
  }
  ['pointerdown', 'pointermove', 'keydown', 'focusin', 'touchstart'].forEach(function (ev) {
    root.addEventListener(ev, bump, { passive: true });
  });
  mascot.addEventListener('mouseenter', function () { if (!busy && !sleeping && !isOpen) act('hola', 1800); });

  /* ---------- chat ---------- */
  var history = [], greeted = false, csrf = null, greeting = Promise.resolve();
  function scroll() { log.scrollTop = log.scrollHeight; }
  function addUser(text) {
    var bubble = el('div', 'nori-msg nori-msg--user', text);
    log.appendChild(bubble);
    history.push({ role: 'user', text: text }); scroll();
    return bubble;
  }
  function typing() {
    var t = el('div', 'nori-msg nori-msg--bot nori-msg--typing');
    t.setAttribute('role', 'status');
    t.appendChild(el('span', 'nori-sr', 'Nori está escribiendo'));
    for (var i = 0; i < 3; i++) { var d = el('i'); d.style.setProperty('--i', i); d.setAttribute('aria-hidden', 'true'); t.appendChild(d); }
    log.appendChild(t); scroll();
    return t;
  }
  function wait(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  /* El texto completo va en un span solo para lectores de pantalla; el que se "escribe" es decorativo. */
  function typeInto(bubble, text) {
    var full = el('span', 'nori-sr', text);
    var shown = el('span'); shown.setAttribute('aria-hidden', 'true');
    bubble.appendChild(full); bubble.appendChild(shown);
    if (reduce()) { shown.textContent = text; scroll(); return Promise.resolve(); }
    var step = Math.max(1, Math.ceil(text.length / 150)), i = 0;
    return new Promise(function (resolve) {
      (function tick() {
        i = Math.min(text.length, i + step);
        shown.textContent = text.slice(0, i); scroll();
        if (i < text.length) setTimeout(tick, 16); else resolve();
      })();
    });
  }

  function addChips(items, makeChip) {
    if (!items || !items.length) return;
    var box = el('div', 'nori__chips');
    items.forEach(function (it) { var c = makeChip(it); if (c) box.appendChild(c); });
    if (box.childNodes.length) { log.appendChild(box); scroll(); }
  }
  function quickChip(label) {
    if (typeof label !== 'string') return null;
    var b = el('button', 'nori-chip', label); b.type = 'button';
    b.addEventListener('click', function () { if (!busy) submit(label); });
    return b;
  }
  function linkChip(l) {
    var href = l && safeHref(l.href);
    if (!href || typeof l.label !== 'string') return null;
    var a = el('a', 'nori-chip nori-chip--link', l.label);
    a.href = href;
    return a;
  }

  async function botSay(text, opts) {
    opts = opts || {};
    var bubble = el('div', 'nori-msg nori-msg--bot');
    log.appendChild(bubble);
    setPose('hablando');
    await typeInto(bubble, text);
    history.push({ role: 'bot', text: text });
    addChips(opts.links, linkChip);
    addChips(opts.suggestions, quickChip);
    var mood = MOODS[opts.mood] ? opts.mood : 'reposo';
    // la pose del ánimo se queda un momento y luego Nori descansa
    if (mood === 'reposo') act('reposo'); else act(mood, 2600);
  }

  /* ---------- comunicación con el backend (CSRF activo) ---------- */
  async function fetchJson(url, options, timeoutMs) {
    var ctl = new AbortController();
    var to = setTimeout(function () { ctl.abort(); }, timeoutMs);
    try {
      options.signal = ctl.signal;
      options.credentials = 'same-origin';
      var res = await fetch(url, options);
      var data = null;
      try { data = await res.json(); } catch (e) { data = null; }
      return { status: res.status, data: data };
    } finally { clearTimeout(to); }
  }
  async function refreshCsrf() {
    var r = await fetchJson(CSRF_URL, { method: 'GET', headers: { 'Accept': 'application/json' } }, 8000);
    csrf = r.data && typeof r.data.csrf === 'string' ? r.data.csrf : null;
    return csrf;
  }
  async function askNori(text) {
    if (!csrf) await refreshCsrf();
    var post = function () {
      return fetchJson(ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf || '' },
        body: JSON.stringify({ message: text })
      }, 20000);
    };
    var r = await post();
    if (r.data && r.data.error === 'csrf') {          // el token venció: se pide uno nuevo y se reintenta una vez
      csrf = typeof r.data.csrf === 'string' ? r.data.csrf : await refreshCsrf();
      r = await post();
    }
    if (r.data && typeof r.data.csrf === 'string') csrf = r.data.csrf;
    if (!r.data || typeof r.data.reply !== 'string') return { reply: OFFLINE, mood: 'confundido' };
    return r.data;
  }

  async function submit(text) {
    text = (text || '').trim().slice(0, MAX_CHARS);
    if (!text) return;
    await greeting;                                       // si Nori aún está saludando, el mensaje espera su turno
    if (busy) return;
    busy = true; send.disabled = true; input.value = '';
    sleeping = false;
    log.querySelectorAll('.nori__chips').forEach(function (c) { c.remove(); });
    var userBubble = addUser(text);
    var t0 = Date.now();
    var ind = typing();
    var my = ++token;
    setPose('pensando');
    var longWait = setTimeout(function () { if (my === token) setPose('escribiendo'); }, 1400);
    var data;
    try {
      data = await askNori(text);
    } catch (e) {
      data = { reply: OFFLINE, mood: 'confundido' };
    }
    clearTimeout(longWait);
    if (data.redact) {                                  // datos sensibles: no se muestran ni se recuerdan
      userBubble.textContent = 'Mensaje oculto por contener datos sensibles.';
      history.pop();
    }
    var left = 800 - (Date.now() - t0);
    if (left > 0) await wait(left);                       // que se alcance a ver a Nori pensando
    ind.remove();
    await botSay(String(data.reply || ''), { mood: data.mood, links: data.links, suggestions: data.suggestions });
    busy = false; send.disabled = false;
    if (isOpen) input.focus();
    bump();
  }
  form.addEventListener('submit', function (e) { e.preventDefault(); submit(input.value); });

  /* ---------- abrir / cerrar ---------- */
  function hideTip() { root.classList.remove('is-tip'); }
  async function setOpen(v) {
    isOpen = v; panel.hidden = !v;
    mascot.setAttribute('aria-expanded', String(v));
    mascot.setAttribute('aria-label', v ? 'Cerrar chat con Nori' : 'Abrir chat con Nori');
    if (v) {
      hideTip(); sleeping = false; act('hola', 1700);
      if (!csrf) refreshCsrf().catch(function () {});     // el token se pide al abrir, no al cargar la página
      if (!greeted) {
        greeted = true; busy = true;
        greeting = (async function () {
          await wait(450);
          await botSay('¡Hola! Soy Nori, tu guía en Norte. Pregúntame cómo ingresar, abrir una cuenta o cómo cuidar tu seguridad.',
            { mood: 'hola', suggestions: QUICK });
          busy = false;
        })();
      }
      input.focus();
    } else {
      act('hola', 1000);
    }
  }
  mascot.addEventListener('click', function () { setOpen(!isOpen); });
  close.addEventListener('click', function () { setOpen(false); mascot.focus(); });
  root.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && isOpen) { setOpen(false); mascot.focus(); }
  });

  /* ---------- arranque: patrón armed → play ---------- */
  setPose('hola');
  root.hidden = false;
  root.classList.add('nori--armed');
  requestAnimationFrame(function () {
    requestAnimationFrame(function () {
      root.classList.add('nori--play');
      act('hola', 3400);
      // el globo de saludo se muestra una sola vez por sesión del navegador para no tapar contenido en cada página
      var tipSeen = false;
      try { tipSeen = window.sessionStorage.getItem('nori:v1:tip') === '1'; window.sessionStorage.setItem('nori:v1:tip', '1'); } catch (e) { tipSeen = false; }
      if (!tipSeen) {
        setTimeout(function () { if (!isOpen) root.classList.add('is-tip'); }, 1100);
        setTimeout(hideTip, 7600);
      }
    });
  });
  bump();

  /* API mínima para pruebas */
  window.Nori = {
    pose: function (n, ms) { if (POSES[n]) { sleeping = false; act(n, ms || 0); } },
    poses: Object.keys(POSES),
    open: function () { if (!isOpen) setOpen(true); },
    close: function () { if (isOpen) setOpen(false); }
  };
})();
