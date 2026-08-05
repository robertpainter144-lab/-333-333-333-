import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vite';

export default defineConfig({
  plugins: [sveltekit()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    // Allow access via Cloudflare quick tunnels (and any other host in dev).
    // Tighten when we move to a fixed production hostname.
    allowedHosts: true
  }
});
