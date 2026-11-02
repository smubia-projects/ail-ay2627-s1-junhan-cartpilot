// AI Lodge showcase notices: the rate-limit CTA (HTTP 429) and the "demo paused" notice (HTTP 503 from the
// shared limiter's kill switch). The rest of the app keeps working either way.
// A classic script (no modules): app.js calls showRateLimitCTA / showDemoPaused.

const PROJECT_CTA = {
  name: 'CartPilot',
  emoji: '🛒',
  accentColor: '#4f46e5',
  headline: 'Basket full!',
  description:
    'You used this demo {count} times: AI agents planned the recipe and compared three stores for you. Want to build AI agents that do real errands? Join us and build projects like this.',
  programme: 'AI Lodge',
  programmeLink: 'https://www.smubia.com/ailodge',
  showcaseLink: 'https://www.smubia.com/Projects',
  githubLink: 'https://github.com/smubia-projects/ail-ay2627-s1-junhan-cartpilot',
};

const STYLE = `
.rlcta-backdrop{position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,.6);backdrop-filter:blur(12px);animation:rlcta-fade .5s ease both}
.rlcta-card{width:min(420px,calc(100vw - 32px));border-radius:24px;overflow:hidden;background:#111113;border:1px solid rgba(255,255,255,.08);box-shadow:0 24px 80px rgba(0,0,0,.6);animation:rlcta-up .6s cubic-bezier(.22,1.2,.36,1) both;font-family:inherit}
.rlcta-top{position:relative;padding:32px 28px;overflow:hidden}
.rlcta-top::before,.rlcta-top::after{content:"";position:absolute;border-radius:50%;background:rgba(255,255,255,.12)}
.rlcta-top::before{top:-40px;right:-40px;width:140px;height:140px}
.rlcta-top::after{bottom:-50px;left:-30px;width:120px;height:120px;background:rgba(255,255,255,.08)}
.rlcta-emoji{font-size:36px;margin-bottom:10px}
.rlcta-head{font-size:20px;font-weight:800;color:#fff;margin-bottom:8px}
.rlcta-desc{font-size:13px;color:rgba(255,255,255,.85);line-height:1.5}
.rlcta-body{padding:24px 28px 16px;display:flex;flex-direction:column;gap:10px}
.rlcta-btn{position:relative;display:flex;align-items:center;justify-content:center;width:100%;box-sizing:border-box;padding:14px 44px;border-radius:14px;font-weight:700;font-size:13px;text-decoration:none;color:#e5e5e5;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.1)}
.rlcta-btn span{position:absolute;right:18px}
.rlcta-btn.primary{color:#fff;border:0}
.rlcta-later{display:block;margin:0 auto 22px;background:none;border:0;color:#777;font-size:12px;font-weight:600;cursor:pointer;padding:8px}
.rlcta-paused{width:min(380px,calc(100vw - 32px));border-radius:20px;background:#111113;border:1px solid rgba(255,255,255,.08);padding:28px;text-align:center;color:#aaa;font-size:13px;line-height:1.5}
.rlcta-paused b{display:block;font-size:16px;color:#fff;margin:12px 0 8px}
.rlcta-paused button{margin-top:20px;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.1);color:#e5e5e5;font-size:12px;font-weight:600;cursor:pointer;padding:10px 24px;border-radius:12px}
@keyframes rlcta-fade{from{opacity:0}to{opacity:1}}
@keyframes rlcta-up{from{transform:translateY(24px) scale(.95);opacity:0}to{transform:none;opacity:1}}
`;

const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

function mount(html, label) {
  if (!document.getElementById('rlcta-style')) {
    const st = document.createElement('style');
    st.id = 'rlcta-style';
    st.textContent = STYLE;
    document.head.appendChild(st);
  }
  document.querySelector('.rlcta-backdrop')?.remove();
  const back = document.createElement('div');
  back.className = 'rlcta-backdrop';
  back.innerHTML = html;
  const card = back.firstElementChild;
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-modal', 'true');
  card.setAttribute('aria-label', label);
  const close = () => {
    back.remove();
    document.removeEventListener('keydown', onKey);
  };
  const focusables = () => [...card.querySelectorAll('a, button')];
  const onKey = (e) => {
    if (e.key === 'Escape') return close();
    if (e.key !== 'Tab') return;
    const f = focusables();
    if (e.shiftKey && document.activeElement === f[0]) (e.preventDefault(), f[f.length - 1].focus());
    else if (!e.shiftKey && document.activeElement === f[f.length - 1]) (e.preventDefault(), f[0].focus());
  };
  back.addEventListener('click', (e) => e.target === back && close());
  card.querySelector('[data-close]').addEventListener('click', close);
  document.addEventListener('keydown', onKey);
  document.body.appendChild(back);
  focusables()[0]?.focus();
}

/** 429: the visitor reached this demo's limit. `count` comes from the API's queries_used. */
function showRateLimitCTA(count) {
  const p = PROJECT_CTA;
  const link = (href, text, primary) =>
    `<a class="rlcta-btn${primary ? ' primary' : ''}" href="${href}" target="_blank" rel="noopener noreferrer"${primary ? ` style="background:linear-gradient(135deg,${p.accentColor},${p.accentColor}AA)"` : ''}>${text}<span>→</span></a>`;
  mount(
    `<div class="rlcta-card">
      <div class="rlcta-top" style="background:linear-gradient(135deg,${p.accentColor},${p.accentColor}66)">
        <div class="rlcta-emoji">${p.emoji}</div>
        <div class="rlcta-head">${esc(p.headline)}</div>
        <div class="rlcta-desc">${esc(p.description.replace('{count}', String(count)))}</div>
      </div>
      <div class="rlcta-body">
        ${link(p.programmeLink, `Join ${esc(p.programme)}`, true)}
        ${link(p.showcaseLink, 'Explore other projects')}
        ${link(p.githubLink, 'View on GitHub')}
      </div>
      <button class="rlcta-later" data-close>Maybe later</button>
    </div>`,
    `${p.name} rate limit reached`,
  );
}

/** 503: an operator paused the demo. Deliberately not the recruitment CTA. */
function showDemoPaused(message) {
  mount(
    `<div class="rlcta-paused">
      <div style="font-size:32px">⏸️</div>
      <b>Demo temporarily paused</b>
      ${esc(message || 'This demo is temporarily paused. Check back soon.')}
      <br /><button data-close>Got it</button>
    </div>`,
    'Demo paused',
  );
}
