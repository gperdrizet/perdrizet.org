// @ts-check
import { defineConfig } from 'astro/config';

import tailwindcss from '@tailwindcss/vite';

import mdx from '@astrojs/mdx';

import sitemap from '@astrojs/sitemap';

// https://astro.build/config
// site URL is read from config.yaml at build time via src/lib/config.ts
export default defineConfig({
  site: 'https://perdrizet.org',
  vite: {
    plugins: [tailwindcss()]
  },
  integrations: [mdx(), sitemap()]
});