// Hero animations: lightweight particle background + reveal helpers
// Node-network background: softly glowing nodes with orbiting satellites, lines and pulses
(function () {
  const canvas = document.getElementById('hero-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  let w = 0, h = 0;
  let nodes = [];
  let pulses = [];
  let mouse = { x: null, y: null };

  function resize() {
    const rect = canvas.getBoundingClientRect();
    w = Math.max(300, Math.floor(rect.width));
    h = Math.max(200, Math.floor(rect.height));
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(w * dpr);
    canvas.height = Math.floor(h * dpr);
    canvas.style.width = w + 'px';
    canvas.style.height = h + 'px';
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function makeNodes() {
    nodes = [];
    const areaFactor = (w * h) / 120000;
    const count = Math.max(10, Math.min(36, Math.floor(areaFactor * 6)));
    for (let i = 0; i < count; i++) {
      const x = Math.random() * w;
      const y = Math.random() * h;
      const r = 8 + Math.random() * 24;
      const hue = 160 + Math.random() * 120; // teal -> blue-ish
      const orbiters = [];
      const orbCount = Math.random() < 0.6 ? 0 : (1 + Math.floor(Math.random() * 3));
      for (let j = 0; j < orbCount; j++) {
        orbiters.push({
          dist: r + 6 + Math.random() * 26,
          angle: Math.random() * Math.PI * 2,
          speed: (0.6 + Math.random() * 1.2) * (Math.random() < 0.5 ? -1 : 1),
          size: 2 + Math.random() * 3
        });
      }
      nodes.push({ x, y, r, hue, orbiters, vx: (Math.random()-0.5)*0.3, vy: (Math.random()-0.5)*0.3 });
    }
  }

  function spawnPulse(x, y) {
    pulses.push({ x, y, t: 0, max: Math.max(w,h) * (0.06 + Math.random()*0.12), alpha: 0.35 + Math.random()*0.25 });
    if (pulses.length > 6) pulses.shift();
  }

  function drawBackground() {
    // gentle radial wash for depth
    const g = ctx.createLinearGradient(0, 0, w, h);
    g.addColorStop(0, 'rgba(8,12,20,0.08)');
    g.addColorStop(0.5, 'rgba(6,10,18,0.12)');
    g.addColorStop(1, 'rgba(6,10,18,0.16)');
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    // small subtle vignette to focus center
    const vg = ctx.createRadialGradient(w/2, h/2, Math.min(w,h)*0.2, w/2, h/2, Math.max(w,h)/1.1);
    vg.addColorStop(0, 'rgba(0,0,0,0)');
    vg.addColorStop(1, 'rgba(0,0,0,0.14)');
    ctx.fillStyle = vg;
    ctx.fillRect(0, 0, w, h);
  }

  function drawNodes(time) {
    // update and render nodes
    nodes.forEach((n, idx) => {
      // gentle drifting
      n.x += n.vx * (0.8 + Math.sin(time/520 + idx) * 0.6);
      n.y += n.vy * (0.8 + Math.cos(time/620 + idx) * 0.6);

      // stay in bounds
      if (n.x < -60) n.x = w + 60; if (n.x > w + 60) n.x = -60;
      if (n.y < -60) n.y = h + 60; if (n.y > h + 60) n.y = -60;

      // node glow
      const grad = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 4.2);
      grad.addColorStop(0, `hsla(${n.hue}, 90%, 60%, 1)`);
      grad.addColorStop(0.18, `hsla(${n.hue}, 78%, 55%, 0.26)`);
      grad.addColorStop(0.55, `hsla(${n.hue}, 70%, 45%, 0.08)`);
      grad.addColorStop(1, 'rgba(8,12,20,0)');
      ctx.fillStyle = grad;
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r * 2.4, 0, Math.PI*2); ctx.fill();

      // core circle
      ctx.fillStyle = `hsla(${n.hue}, 90%, 55%, 0.98)`;
      ctx.beginPath(); ctx.arc(n.x, n.y, n.r * 0.9, 0, Math.PI*2); ctx.fill();

      // orbiters (satellites) with faint trails
      n.orbiters.forEach((orb) => {
        orb.angle += orb.speed * 0.008;
        const ox = n.x + Math.cos(orb.angle) * orb.dist;
        const oy = n.y + Math.sin(orb.angle) * orb.dist;
        ctx.fillStyle = `rgba(220,235,255,0.95)`;
        ctx.beginPath(); ctx.arc(ox, oy, orb.size, 0, Math.PI*2); ctx.fill();
        // tiny trail
        ctx.strokeStyle = `rgba(170,210,240,0.06)`;
        ctx.beginPath(); ctx.moveTo(n.x, n.y); ctx.lineTo(ox, oy); ctx.stroke();
      });
    });
  }

  function drawConnections() {
    // connect near nodes with faint lines
    const maxDist = Math.max(120, Math.min(260, (w+h)/12));
    ctx.lineWidth = 0.6;
    for (let i = 0; i < nodes.length; i++) {
      for (let j = i+1; j < nodes.length; j++) {
        const a = nodes[i], b = nodes[j];
        const dx = a.x - b.x, dy = a.y - b.y;
        const d = Math.sqrt(dx*dx + dy*dy);
        if (d < maxDist) {
          const t = 1 - d / maxDist;
          ctx.strokeStyle = `rgba(160,220,255,${0.06 * t})`;
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        }
      }
    }
  }

  function drawPulses() {
    for (let i = pulses.length -1; i >=0; i--) {
      const p = pulses[i];
      p.t += 1;
      const progress = p.t / 90;
      if (progress > 1) { pulses.splice(i,1); continue; }
      ctx.strokeStyle = `rgba(140,220,255,${p.alpha * (1 - progress)})`;
      ctx.lineWidth = 1 + 8 * (1 - progress);
      ctx.beginPath(); ctx.arc(p.x, p.y, p.max * progress, 0, Math.PI*2); ctx.stroke();
      // subtle inner glow for impact
      ctx.fillStyle = `rgba(160,230,255,${0.03 * (1-progress)})`;
      ctx.beginPath(); ctx.arc(p.x, p.y, Math.max(6, p.max * progress * 0.06), 0, Math.PI*2); ctx.fill();
    }
  }

  function frame(time) {
    ctx.clearRect(0,0,w,h);
    drawBackground();

    // parallax offset from mouse
    const px = (mouse.x !== null) ? (mouse.x - w/2) * 0.02 : 0;
    const py = (mouse.y !== null) ? (mouse.y - h/2) * 0.02 : 0;
    ctx.save();
    ctx.translate(px, py);

    drawConnections();
    drawNodes(time || 0);
    drawPulses();

    ctx.restore();
    requestAnimationFrame(frame);
  }

  // occasional pulses at random nodes to add life (a bit more frequent)
  let pulseTimer = 0;
  function pulseTick() {
    pulseTimer++;
    if (pulseTimer > 28 + Math.random() * 120) {
      const n = nodes[Math.floor(Math.random() * nodes.length)];
      if (n) spawnPulse(n.x, n.y);
      pulseTimer = 0;
    }
    setTimeout(pulseTick, 300 + Math.random()*600);
  }

  function start() {
    resize();
    makeNodes();
    pulseTimer = 0; pulseTick();
    requestAnimationFrame(frame);
  }

  function stop() {
    // nothing heavy to teardown; rely on observers to pause animation by removing listeners if needed
  }

  window.addEventListener('resize', () => { resize(); makeNodes(); });
  window.addEventListener('mousemove', (e) => {
    const rect = canvas.getBoundingClientRect(); mouse.x = e.clientX - rect.left; mouse.y = e.clientY - rect.top;
  });
  window.addEventListener('mouseleave', () => { mouse.x = null; mouse.y = null; });

  // Start when visible
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(ent => {
      if (ent.isIntersecting) start(); else stop();
    });
  }, { threshold: 0.05 });
  obs.observe(canvas);

})();
  // Simple stagger reveal for items with class .stagger-item
  function revealStagger() {
    const items = Array.from(document.querySelectorAll('.stagger-item'));
    if (!items.length) return;
    const io = new IntersectionObserver((entries, observer) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const baseIndex = items.indexOf(entry.target);
        // reveal a window of nearby items for a nice stagger
        for (let i = baseIndex; i < Math.min(items.length, baseIndex + 6); i++) {
          const el = items[i];
          setTimeout(() => el.classList.add('revealed'), (i - baseIndex) * 90);
        }
        observer.unobserve(entry.target);
      });
    }, { threshold: 0.06 });

    items.forEach((it) => io.observe(it));
  }

  // small enhancement: pulse stat when updated
  function watchStatPulse() {
    const statIds = ['stat-helped', 'stat-requests', 'stat-hours'];
    statIds.forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      const observer = new MutationObserver(() => {
        el.animate([{ transform: 'translateY(-6px)', opacity: 0.96 }, { transform: 'translateY(0)', opacity: 1 }], { duration: 420, easing: 'cubic-bezier(.2,.9,.2,1)' });
      });
      observer.observe(el, { childList: true });
    });
  }

  // Initialize small helpers on DOMContentLoaded
  document.addEventListener('DOMContentLoaded', function () {
    revealStagger();
    watchStatPulse();
    // add a11y attribute to canvas
    if (canvas) canvas.setAttribute('aria-hidden', 'true');
  });
})();
