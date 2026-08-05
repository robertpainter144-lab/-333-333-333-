<script lang="ts">
  import { page } from '$app/stores';
  import { backendUrl, apiToken, isOnboarded } from '$lib/auth';
  import Onboarding from './_components/Onboarding.svelte';

  let { children } = $props();

  // Derived: are we onboarded?
  let onboarded = $derived(isOnboarded($backendUrl, $apiToken));

  const tabs = [
    { href: '/', label: 'Signals', icon: '◐' },
    { href: '/portfolio/', label: 'Portfolio', icon: '◧' },
    { href: '/settings/', label: 'Settings', icon: '⚙' }
  ];
</script>

<svelte:head>
  <title>Companion</title>
</svelte:head>

<main class="app">
  {#if !onboarded && $page.url.pathname !== '/settings/'}
    <Onboarding />
  {:else}
    <div class="content">
      {@render children()}
    </div>
    <nav class="tabbar">
      {#each tabs as t}
        <a
          href={t.href}
          class:active={$page.url.pathname === t.href}
        >
          <span class="icon">{t.icon}</span>
          <span class="label">{t.label}</span>
        </a>
      {/each}
    </nav>
  {/if}
</main>

<style>
  :global(html, body) {
    margin: 0;
    padding: 0;
    background: #0f172a;
    color: #e2e8f0;
    font-family: -apple-system, BlinkMacSystemFont, 'SF Pro Text', system-ui, sans-serif;
    -webkit-font-smoothing: antialiased;
  }

  :global(*) {
    box-sizing: border-box;
  }

  .app {
    display: flex;
    flex-direction: column;
    min-height: 100vh;
    min-height: 100dvh;
  }

  .content {
    flex: 1;
    overflow-y: auto;
    padding-bottom: calc(env(safe-area-inset-bottom) + 70px);
  }

  .tabbar {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    display: flex;
    background: rgba(15, 23, 42, 0.95);
    backdrop-filter: blur(12px);
    border-top: 1px solid #1e293b;
    padding-bottom: env(safe-area-inset-bottom);
    z-index: 100;
  }

  .tabbar a {
    flex: 1;
    text-align: center;
    padding: 10px 0;
    color: #64748b;
    text-decoration: none;
    font-size: 11px;
  }

  .tabbar a.active {
    color: #60a5fa;
  }

  .tabbar .icon {
    display: block;
    font-size: 22px;
    line-height: 1;
    margin-bottom: 2px;
  }
</style>
