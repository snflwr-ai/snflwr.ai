import satori from 'satori';
import sharp from 'sharp';
import { readFileSync } from 'fs';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';

const __dirname = dirname(fileURLToPath(import.meta.url));
const fontsDir = join(__dirname, '../node_modules/@fontsource/inter/files');

const interSemiBold = readFileSync(join(fontsDir, 'inter-latin-600-normal.woff'));
const interBold = readFileSync(join(fontsDir, 'inter-latin-700-normal.woff'));
const interExtraBold = readFileSync(join(fontsDir, 'inter-latin-800-normal.woff'));

const svg = await satori(
  {
    type: 'div',
    props: {
      style: {
        width: '100%',
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        background: '#111827',
        fontFamily: 'Inter',
      },
      children: [
        // Sunflower
        {
          type: 'div',
          props: {
            style: { fontSize: 72, marginBottom: 24 },
            children: '🌻',
          },
        },
        // Brand name
        {
          type: 'div',
          props: {
            style: {
              fontSize: 80,
              fontWeight: 800,
              color: '#FFFFFF',
              letterSpacing: '-2px',
            },
            children: 'snflwr.ai',
          },
        },
        // Tagline
        {
          type: 'div',
          props: {
            style: {
              fontSize: 36,
              fontWeight: 600,
              color: '#F59E0B',
              marginTop: 16,
            },
            children: 'Safe AI for K-12',
          },
        },
        // Subtitle
        {
          type: 'div',
          props: {
            style: {
              fontSize: 22,
              color: '#9CA3AF',
              marginTop: 20,
            },
            children: 'Your child talks to AI. You control what it says back.',
          },
        },
        // Accent line
        {
          type: 'div',
          props: {
            style: {
              width: 200,
              height: 3,
              background: '#F59E0B',
              opacity: 0.5,
              borderRadius: 2,
              marginTop: 40,
            },
          },
        },
      ],
    },
  },
  {
    width: 1200,
    height: 630,
    fonts: [
      { name: 'Inter', data: interSemiBold, weight: 600 },
      { name: 'Inter', data: interBold, weight: 700 },
      { name: 'Inter', data: interExtraBold, weight: 800 },
    ],
  }
);

await sharp(Buffer.from(svg)).png().toFile('public/og-image.png');
console.log('Generated public/og-image.png (1200x630)');
