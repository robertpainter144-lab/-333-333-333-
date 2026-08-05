<script lang="ts">
  function open(url: string) {
    window.open(url, '_blank', 'noopener');
  }

  const alpacaDone = $derived(
    typeof localStorage !== 'undefined' && localStorage.getItem('done_alpaca') === 'true'
  );
  const ibkrDone = $derived(
    typeof localStorage !== 'undefined' && localStorage.getItem('done_ibkr') === 'true'
  );
  const qcLinkDone = $derived(
    typeof localStorage !== 'undefined' && localStorage.getItem('done_qc_link') === 'true'
  );

  const anyBroker = $derived(alpacaDone || ibkrDone);
</script>

<header><h1>Portfolio</h1></header>

{#if !anyBroker}
  <section class="empty">
    <h2>Nothing here yet</h2>
    <p>
      You haven't set up a brokerage. Once you do, this tab will link out to
      your live positions, P&L, and order history at the broker.
    </p>
    <a class="cta" href="/settings/">Open setup →</a>
  </section>
{:else}
  <section>
    <h2>Your dashboards</h2>
    <p class="body">
      Your positions and P&L live at your broker (we don't store any of that
      here). Tap one of these to open the live dashboard in a new tab.
    </p>

    {#if alpacaDone}
      <div class="card">
        <div class="card-head">
          <span class="badge paper">PAPER</span>
          <span>Alpaca</span>
        </div>
        <div class="actions">
          <button class="primary" onclick={() => open('https://app.alpaca.markets/paper/dashboard/overview')}>
            Alpaca dashboard ↗
          </button>
          <button onclick={() => open('https://app.alpaca.markets/paper/dashboard/orders')}>
            Orders ↗
          </button>
        </div>
      </div>
    {/if}

    {#if ibkrDone}
      <div class="card">
        <div class="card-head">
          <span class="badge live">REAL</span>
          <span>Interactive Brokers</span>
        </div>
        <div class="actions">
          <button class="primary" onclick={() => open('https://www.interactivebrokers.com/portal')}>
            IBKR Client Portal ↗
          </button>
          {#if qcLinkDone}
            <button onclick={() => open('https://www.quantconnect.com/live')}>
              QC Live Results ↗
            </button>
          {/if}
        </div>
      </div>
    {/if}
  </section>
{/if}

<style>
  header { padding: 14px 20px 4px; }
  h1 { margin: 0; font-size: 22px; font-weight: 700; }
  section {
    margin: 14px 12px;
    padding: 14px 16px;
    background: #1e293b;
    border-radius: 14px;
  }
  section.empty { text-align: center; padding: 40px 24px; }
  section.empty h2 { margin: 0 0 8px; font-size: 18px; }
  section.empty p { color: #94a3b8; font-size: 14px; line-height: 1.6; margin: 0 0 18px; }
  h2 { margin: 0 0 8px; font-size: 13px; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }
  .body { color: #cbd5e1; font-size: 14px; line-height: 1.6; margin: 0 0 12px; }
  .cta {
    display: inline-block;
    background: #2563eb;
    color: white;
    text-decoration: none;
    padding: 12px 22px;
    border-radius: 10px;
    font-weight: 600;
  }
  .card {
    background: #0f172a;
    border-radius: 12px;
    padding: 12px 14px;
    margin-bottom: 10px;
  }
  .card-head {
    display: flex; gap: 8px; align-items: center;
    font-weight: 600; margin-bottom: 8px;
  }
  .badge {
    font-size: 10px; font-weight: 700; letter-spacing: 0.05em;
    padding: 3px 6px; border-radius: 4px;
  }
  .badge.paper { background: #1e40af; color: #dbeafe; }
  .badge.live  { background: #7c2d12; color: #fed7aa; }
  .actions { display: flex; flex-wrap: wrap; gap: 6px; }
  button {
    background: #334155;
    color: #e2e8f0;
    border: 0;
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 14px;
    cursor: pointer;
  }
  button.primary { background: #2563eb; font-weight: 600; }
</style>
