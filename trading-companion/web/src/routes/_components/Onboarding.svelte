<script lang="ts">
  import { backendUrl, apiToken } from '$lib/auth';

  let url = $state('');
  let token = $state('');
  let error = $state<string | null>(null);

  function save() {
    if (!url || !token) return;
    error = null;
    let normalized = url.trim();
    if (!/^https?:\/\//i.test(normalized)) normalized = 'https://' + normalized;
    normalized = normalized.replace(/\/$/, '');
    try {
      new URL(normalized);
    } catch {
      error = 'Not a valid URL.';
      return;
    }
    backendUrl.set(normalized);
    apiToken.set(token);
  }
</script>

<div class="wrap">
  <h1>Set up</h1>
  <p class="hint">
    Enter the backend URL and API token Robert shared with you. They're stored
    only on this device.
  </p>

  <label>
    <span>Backend URL</span>
    <input
      type="url"
      bind:value={url}
      placeholder="https://companion.example.com"
      autocomplete="off"
      autocapitalize="none"
      inputmode="url"
    />
  </label>

  <label>
    <span>API token</span>
    <input type="password" bind:value={token} autocomplete="off" />
  </label>

  <button onclick={save} disabled={!url || !token}>Save and continue</button>

  {#if error}
    <p class="err">{error}</p>
  {/if}
</div>

<style>
  .wrap {
    padding: 32px 20px;
    max-width: 480px;
    margin: 0 auto;
  }
  h1 {
    margin-top: 0;
    font-weight: 600;
  }
  .hint {
    color: #94a3b8;
    font-size: 14px;
    line-height: 1.5;
  }
  label {
    display: block;
    margin: 16px 0;
  }
  label span {
    display: block;
    font-size: 12px;
    color: #94a3b8;
    margin-bottom: 6px;
    text-transform: uppercase;
    letter-spacing: 0.05em;
  }
  input {
    width: 100%;
    background: #1e293b;
    border: 1px solid #334155;
    border-radius: 10px;
    padding: 12px 14px;
    color: #e2e8f0;
    font-size: 16px;
  }
  input:focus {
    outline: none;
    border-color: #60a5fa;
  }
  button {
    width: 100%;
    margin-top: 12px;
    padding: 14px;
    background: #2563eb;
    color: white;
    border: 0;
    border-radius: 10px;
    font-size: 16px;
    font-weight: 600;
  }
  button:disabled {
    opacity: 0.4;
  }
  .err {
    color: #fca5a5;
    font-size: 13px;
    margin-top: 12px;
    text-align: center;
  }
</style>
