<script lang="ts">
  import { backendUrl, apiToken } from '$lib/auth';

  type Step = 'alpaca' | 'ibkr' | 'qc' | 'qc_link';

  function getFlag(k: Step): boolean {
    return typeof localStorage !== 'undefined' && localStorage.getItem(`done_${k}`) === 'true';
  }
  function setFlag(k: Step, v: boolean) {
    if (typeof localStorage === 'undefined') return;
    if (v) localStorage.setItem(`done_${k}`, 'true');
    else localStorage.removeItem(`done_${k}`);
  }

  let alpacaDone = $state(getFlag('alpaca'));
  let ibkrDone = $state(getFlag('ibkr'));
  let qcDone = $state(getFlag('qc'));
  let qcLinkDone = $state(getFlag('qc_link'));

  function mark(k: Step, v: boolean) {
    setFlag(k, v);
    if (k === 'alpaca') alpacaDone = v;
    if (k === 'ibkr') ibkrDone = v;
    if (k === 'qc') qcDone = v;
    if (k === 'qc_link') qcLinkDone = v;
  }

  function open(url: string) {
    window.open(url, '_blank', 'noopener');
  }

  function reset() {
    if (!confirm('Clear all settings + setup progress on this device?')) return;
    backendUrl.set(null);
    apiToken.set(null);
    if (typeof localStorage !== 'undefined') {
      for (const k of ['alpaca', 'ibkr', 'qc', 'qc_link']) {
        localStorage.removeItem(`done_${k}`);
      }
    }
    window.location.href = '/';
  }

  // Show advanced (IBKR + QC) section only if user has expanded it, or has any IBKR/QC progress.
  let showAdvanced = $state(getFlag('ibkr') || getFlag('qc') || getFlag('qc_link'));
</script>

<header>
  <h1>Get started</h1>
  <p class="lede">
    This app shows Robert's regime signals and helps you set up a broker
    to act on them. Pick the path that fits you — paper to test, IBKR for
    real money.
  </p>
</header>

<section class="path">
  <div class="path-header">
    <span class="badge paper">PAPER</span>
    <h2>Start with paper trading (recommended)</h2>
  </div>
  <p class="body">
    Alpaca paper is a free, fake-money brokerage. Sign up, place orders to
    test what Robert's signals would do, no real money on the line.
  </p>

  <ol class="steps">
    <li class:done={alpacaDone}>
      <div class="step-title">Sign up for Alpaca paper trading</div>
      <div class="step-body">
        Free. They give you $100,000 in fake money to practice with.
      </div>
      <div class="row">
        <button onclick={() => open('https://app.alpaca.markets/signup')}>
          Open Alpaca signup ↗
        </button>
        <button class="ghost" onclick={() => open('https://app.alpaca.markets/login')}>
          I already have one ↗
        </button>
        <button class="check" class:on={alpacaDone} onclick={() => mark('alpaca', !alpacaDone)}>
          {alpacaDone ? '✓ Done' : 'Mark done'}
        </button>
      </div>
    </li>
  </ol>

  {#if alpacaDone}
    <p class="ok-banner">
      You're set up for paper. Open <a href="/">Signals</a> to see what to trade today.
    </p>
  {/if}
</section>

<section class="path">
  <button class="expand" onclick={() => (showAdvanced = !showAdvanced)}>
    <span class="badge live">REAL</span>
    <h2>Live trading with IBKR {showAdvanced ? '▾' : '▸'}</h2>
  </button>

  {#if showAdvanced}
    <p class="body">
      Real money path. Sign up for an Interactive Brokers account, then link it
      to QuantConnect so QC's algorithms can route orders directly to IBKR via
      their permanent FIX connection.
    </p>

    <ol class="steps">
      <li class:done={ibkrDone}>
        <div class="step-title">Open an Interactive Brokers account</div>
        <div class="step-body">
          IBKR Lite is free and works for US stocks. Takes 1–2 days to fund.
        </div>
        <div class="row">
          <button onclick={() => open('https://www.interactivebrokers.com/en/whyib/overview.php')}>
            IBKR signup ↗
          </button>
          <button class="check" class:on={ibkrDone} onclick={() => mark('ibkr', !ibkrDone)}>
            {ibkrDone ? '✓ Done' : 'Mark done'}
          </button>
        </div>
      </li>

      <li class:done={qcDone}>
        <div class="step-title">Sign up for QuantConnect</div>
        <div class="step-body">
          Free tier is enough to link your brokerage. You only need a paid
          plan if you later want QC to run live algorithms 24/7.
        </div>
        <div class="row">
          <button onclick={() => open('https://www.quantconnect.com/signup')}>
            QC signup ↗
          </button>
          <button class="ghost" onclick={() => open('https://www.quantconnect.com/login')}>
            I already have one ↗
          </button>
          <button class="check" class:on={qcDone} onclick={() => mark('qc', !qcDone)}>
            {qcDone ? '✓ Done' : 'Mark done'}
          </button>
        </div>
      </li>

      <li class:done={qcLinkDone}>
        <div class="step-title">Link IBKR inside QuantConnect</div>
        <div class="step-body">
          In QC: <b>Organization → Brokerages → Add Brokerage → Interactive
          Brokers</b>. You'll enter your IBKR username, password, and approve
          the prompt on your IBKR Mobile app.
        </div>
        <div class="row">
          <button onclick={() => open('https://www.quantconnect.com/account')}>
            Open QC account ↗
          </button>
          <button class="check" class:on={qcLinkDone} onclick={() => mark('qc_link', !qcLinkDone)}>
            {qcLinkDone ? '✓ Done' : 'Mark done'}
          </button>
        </div>
      </li>
    </ol>

    {#if ibkrDone && qcDone && qcLinkDone}
      <p class="ok-banner">
        Live trading is wired up. Open <a href="/portfolio/">Portfolio</a> for
        quick links to your QC + IBKR dashboards.
      </p>
    {/if}
  {/if}
</section>

<section>
  <h2>Device</h2>
  <code class="small">{$backendUrl ?? '(not set)'}</code>
  <button class="danger" onclick={reset}>Reset device</button>
</section>

<style>
  header { padding: 14px 20px 4px; }
  h1 { margin: 0; font-size: 22px; font-weight: 700; }
  .lede { color: #94a3b8; font-size: 14px; line-height: 1.5; margin: 6px 0 0; }
  section {
    margin: 14px 12px;
    padding: 14px 16px;
    background: #1e293b;
    border-radius: 14px;
  }
  section.path { padding-top: 12px; }
  h2 { margin: 0 0 6px; font-size: 15px; font-weight: 600; color: #e2e8f0; }
  section > h2:first-of-type:not(:has(.badge)) {
    font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em;
  }
  .body { color: #cbd5e1; font-size: 13px; line-height: 1.5; margin: 4px 0 12px; }

  .path-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
  .badge {
    font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
    padding: 3px 6px; border-radius: 4px;
  }
  .badge.paper { background: #1e40af; color: #dbeafe; }
  .badge.live  { background: #7c2d12; color: #fed7aa; }

  button.expand {
    display: flex; align-items: center; gap: 8px;
    background: transparent; border: 0; padding: 0; cursor: pointer; width: 100%; text-align: left;
  }
  button.expand h2 { flex: 1; }

  .steps { list-style: none; counter-reset: step; padding: 0; margin: 8px 0 0; }
  .steps li {
    counter-increment: step;
    padding: 12px 0 12px 36px;
    position: relative;
    border-top: 1px solid #334155;
  }
  .steps li::before {
    content: counter(step);
    position: absolute;
    left: 0; top: 12px;
    width: 26px; height: 26px;
    background: #334155;
    color: #e2e8f0;
    border-radius: 50%;
    display: grid; place-items: center;
    font-size: 13px; font-weight: 600;
  }
  .steps li.done::before { background: #22c55e; color: #0f172a; content: '✓'; }
  .step-title { font-weight: 600; margin-bottom: 4px; }
  .step-body { color: #94a3b8; font-size: 13px; line-height: 1.5; margin-bottom: 10px; }
  .step-body b { color: #e2e8f0; font-weight: 600; }

  .row { display: flex; flex-wrap: wrap; gap: 6px; }

  button {
    background: #334155;
    color: #e2e8f0;
    border: 0;
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 14px;
    cursor: pointer;
  }
  button.ghost { background: transparent; color: #94a3b8; }
  button.check { background: #0f172a; color: #94a3b8; border: 1px solid #334155; }
  button.check.on { background: #14532d; color: #bbf7d0; border-color: #15803d; }
  button.danger { background: transparent; color: #fca5a5; padding: 8px 0; margin: 8px 0 0; }
  button:disabled { opacity: 0.5; }

  .ok-banner {
    margin: 12px 0 0;
    padding: 10px 12px;
    background: #14532d;
    color: #bbf7d0;
    border-radius: 10px;
    font-size: 13px;
  }
  .ok-banner a { color: #bbf7d0; font-weight: 600; }

  .small { display: block; font-size: 12px; color: #94a3b8; word-break: break-all; margin-bottom: 8px; }
</style>
