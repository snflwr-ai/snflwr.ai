import type { Metadata } from 'next'
import { Inter } from 'next/font/google'
import './globals.css'

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter',
})

export const metadata: Metadata = {
  title: 'snflwr.ai — K-12 Safe AI Learning Platform',
  description:
    'Your child talks to AI. You control what it says back. snflwr.ai wraps Open WebUI with a 5-stage safety pipeline, parental dashboard, and fully offline deployment. No cloud, no accounts.',
  keywords: [
    'K-12 AI',
    'safe AI for kids',
    'educational AI',
    'parental controls AI',
    'offline AI',
    'open source AI safety',
    'snflwr.ai',
  ],
  authors: [{ name: 'snflwr.ai' }],
  openGraph: {
    title: 'snflwr.ai — Safe AI for K-12',
    description:
      'Your child talks to AI. You control what it says back. Runs entirely on your hardware — no cloud, no data leaving your network.',
    url: 'https://snflwr.ai',
    siteName: 'snflwr.ai',
    images: [
      {
        url: '/og-image.png',
        width: 1200,
        height: 630,
        alt: 'snflwr.ai — K-12 Safe AI Learning Platform',
      },
    ],
    locale: 'en_US',
    type: 'website',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'snflwr.ai — Safe AI for K-12',
    description: 'Your child talks to AI. You control what it says back.',
    images: ['/og-image.png'],
  },
  robots: {
    index: true,
    follow: true,
  },
  metadataBase: new URL('https://snflwr.ai'),
}

export default function RootLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <html lang="en" className={inter.variable}>
      <head>
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              '@context': 'https://schema.org',
              '@type': 'Organization',
              name: 'snflwr.ai',
              url: 'https://snflwr.ai',
              description: 'K-12 Safe AI Learning Platform',
              logo: 'https://snflwr.ai/favicon.svg',
            }),
          }}
        />
        <script
          type="application/ld+json"
          dangerouslySetInnerHTML={{
            __html: JSON.stringify({
              '@context': 'https://schema.org',
              '@type': 'SoftwareApplication',
              name: 'snflwr.ai',
              url: 'https://snflwr.ai',
              description:
                'K-12 Safe AI Learning Platform — wraps Open WebUI with a 5-stage safety pipeline, parental dashboard, and fully offline deployment.',
              applicationCategory: 'EducationalApplication',
              license: 'https://www.gnu.org/licenses/agpl-3.0.html',
              operatingSystem: 'Any',
            }),
          }}
        />
      </head>
      <body className={`${inter.className} antialiased`}>{children}</body>
    </html>
  )
}
