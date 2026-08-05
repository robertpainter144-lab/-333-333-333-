<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '$lib/api';
  import type { SignalsResponse } from '$lib/types';

  let data = $state<SignalsResponse | null>(null);
  let error = $state<string | null>(null);
  let loading = $state(true);

  async function load() {
    loading = true;
    error = null;
    try {
      data = await api.signals();
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      loading = false;
    }
  }

  onMount(load);

  function statusColor(status: string): string {
    switch (status.toLowerCase()) {
      case 'active': return '#22c55e';
      case 'rebalance': return '#60a5fa';
      case 'warning': return '#f59e0b';
      default: return '#64748b';
    }
  }
</script>

<header>
  <h1>Signals</h1>
  <button class="refresh" onclick={load} disabled={loading}>↻</button>
</header>

{#if loading && !data}
  <div class="state">Loading…</div>
{:else if error}
  <div class="state error">{error}</div>
{:else if data}
  <div class="meta">As of {new Date(data.as_of).toLocaleString()}</div>
  <ul class="cards">
    {#each data.regimes as r (r.id)}
      <li class="card">
        <div class="card-head">
          <h2>{r.name}</h2>
          <span class="status" style:color={statusColor(r.status)} style:background={`${statusColor(r.status)}22`}>
            {r.status.toUpperCase()}
          </span>
        </div>
        {#if r.rationale}
          <p class="rationale">{r.rationale}</p>
        {/if}
        {#if r.action}
          <div class="action">
            <code>{r.action.side} {r.action.symbol}</code>
            {#if r.action.size_pct_of_buying_power != null}
              <span>{r.action.size_pct_of_buying_power}% BP</span>
            {/if}
          </div>
        {/if}
      </li>
    {/each}
  </ul>
{/if}

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 14px 20px 4px;
  }
  h1 { margin: 0; font-size: 22px; font-weight: 700; }
  .refresh {
    background: transparent;
    color: #94a3b8;
    border: 0;
    font-size: 22px;
    cursor: pointer;
  }
  .meta {
    padding: 0 20px 12px;
    color: #64748b;
    font-size: 12px;
  }
  .state {
    padding: 40px 20px;
    color: #94a3b8;
    text-align: center;
  }
  .state.error { color: #fca5a5; }
  .cards {
    list-style: none;
    margin: 0;
    padding: 0 12px;
  }
  .card {
    background: #1e293b;
    border-radius: 14px;
    padding: 14px 16px;
    margin-bottom: 10px;
  }
  .card-head {
    display: flex;
    justify-content: space-between;
    align-items: center;
  }
  h2 { margin: 0; font-size: 16px; font-weight: 600; }
  .status {
    font-size: 10px;
    padding: 3px 8px;
    border-radius: 6px;
    font-weight: 600;
    letter-spacing: 0.03em;
  }
  .rationale {
    color: #94a3b8;
    font-size: 13px;
    margin: 8px 0 0;
    line-height: 1.5;
  }
  .action {
    margin-top: 10px;
    padding-top: 10px;
    border-top: 1px solid #334155;
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 14px;
  }
  .action code {
    background: transparent;
    color: #e2e8f0;
    font-family: ui-monospace, SFMono-Regular, monospace;
  }
  .action span { color: #94a3b8; font-size: 12px; }
</style>
