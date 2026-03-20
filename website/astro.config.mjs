import { defineConfig } from 'astro/config';
import sitemap from '@astrojs/sitemap';

export default defineConfig({
  site: 'https://snflwr.ai',
  output: 'static',
  integrations: [sitemap()],
});
