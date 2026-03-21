import type { JSX } from 'react'

const navLinks: { heading: string; links: { label: string; href: string; external?: boolean }[] }[] = [
  {
    heading: 'Product',
    links: [
      { label: 'Features', href: '#features' },
      { label: 'How It Works', href: '#how-it-works' },
      { label: 'Safety & Privacy', href: '#safety' },
    ],
  },
  {
    heading: 'Resources',
    links: [
      {
        label: 'GitHub',
        href: 'https://github.com/snflwr-ai/snflwr.ai',
        external: true,
      },
      {
        label: 'Documentation',
        href: 'https://snflwr-ai.github.io/snflwr.ai/',
        external: true,
      },
      {
        label: 'Discussions',
        href: 'https://github.com/snflwr-ai/snflwr.ai/discussions',
        external: true,
      },
    ],
  },
  {
    heading: 'Community',
    links: [
      {
        label: 'Discord',
        href: 'https://discord.gg/5rJgQTnV4s',
        external: true,
      },
      {
        label: 'Issues',
        href: 'https://github.com/snflwr-ai/snflwr.ai/issues',
        external: true,
      },
      {
        label: 'Contributing',
        href: 'https://github.com/snflwr-ai/snflwr.ai/blob/main/CONTRIBUTING.md',
        external: true,
      },
    ],
  },
]

export default function Footer(): JSX.Element {
  return (
    <footer
      className="border-t border-white/[0.06] bg-[#050506]"
      role="contentinfo"
    >
      <div className="max-w-6xl mx-auto px-6 py-14 grid grid-cols-1 md:grid-cols-[1.5fr_1fr] gap-10">
        {/* Brand */}
        <div>
          <a
            href="/"
            className="inline-flex items-center gap-2 text-white font-bold text-lg mb-4 hover:opacity-80 transition-opacity"
            aria-label="snflwr.ai home"
          >
            <span role="img" aria-label="sunflower">🌻</span>
            snflwr.ai
          </a>
          <p className="text-sm text-white/60 leading-relaxed max-w-xs mb-3">
            Built for educators, students, and families who value safety, privacy,
            and local AI.
          </p>
          <p className="text-xs text-white/50">
            Licensed under{' '}
            <a
              href="https://github.com/snflwr-ai/snflwr.ai/blob/main/LICENSE"
              target="_blank"
              rel="noopener noreferrer"
              className="text-white/60 underline hover:text-white/75 transition-colors"
            >
              AGPL-3.0
            </a>
          </p>
        </div>

        {/* Nav columns */}
        <nav
          className="grid grid-cols-3 gap-6"
          aria-label="Footer navigation"
        >
          {navLinks.map((col) => (
            <div key={col.heading}>
              <p className="text-[10px] font-bold uppercase tracking-[0.12em] text-white/50 mb-4">
                {col.heading}
              </p>
              <ul role="list" className="space-y-2.5">
                {col.links.map((link) => (
                  <li key={link.label}>
                    <a
                      href={link.href}
                      {...(link.external
                        ? { target: '_blank', rel: 'noopener noreferrer' }
                        : {})}
                      className="text-sm text-white/60 hover:text-white/80 transition-colors"
                    >
                      {link.label}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
      </div>

      <div className="border-t border-white/[0.04]">
        <div className="max-w-6xl mx-auto px-6 py-5 flex flex-col sm:flex-row justify-between items-center gap-3">
          <p className="text-xs text-white/50">
            © 2025 snflwr.ai · Open source under AGPL-3.0
          </p>
          <p className="text-xs text-white/50">
            Commercial licensing:{' '}
            <a
              href="mailto:licensing@snflwr.ai"
              className="text-white/60 underline hover:text-white/75 transition-colors"
            >
              licensing@snflwr.ai
            </a>
          </p>
        </div>
      </div>
    </footer>
  )
}
