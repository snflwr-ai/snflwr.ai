import sharp from 'sharp';
import { readFileSync } from 'fs';
const svg = readFileSync('public/og-image.svg');
await sharp(svg).resize(1200, 630).png().toFile('public/og-image.png');
console.log('Generated public/og-image.png');
