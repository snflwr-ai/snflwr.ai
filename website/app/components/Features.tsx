'use client'

import { useRef } from 'react'
import { motion, useInView, useReducedMotion, type Variants } from 'framer-motion'

const features = [
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      </svg>
    ),
    title: 'Runs Offline',
    desc: 'No cloud, no accounts, no data leaving your network. Deploy on a USB drive for complete physical data control. All AI inference runs locally via Ollama.',
    accent: false,
  },
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M9 12l2 2 4-4" />
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      </svg>
    ),
    title: 'Fail-Closed Safety',
    desc: '5-stage content pipeline: input validation, normalization, pattern matching, LLM classification, and age-adaptive rules. If any stage errors, content is blocked — never passed through.',
    accent: true,
  },
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <path d="M3 9h18M9 21V9" />
      </svg>
    ),
    title: 'Parent Dashboard',
    desc: 'Real-time monitoring of every conversation. Safety incident alerts, usage analytics, and full chat history review. Know exactly what your child is asking.',
    accent: false,
  },
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
        <circle cx="9" cy="7" r="4" />
        <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
        <path d="M16 3.13a4 4 0 0 1 0 7.75" />
      </svg>
    ),
    title: 'K-5 through 12th Grade',
    desc: 'Age-adaptive filtering per child profile. Content rules tighten for younger students and relax appropriately for older ones — calibrated, not one-size-fits-all.',
    accent: false,
  },
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="2" y="3" width="20" height="14" rx="2" />
        <path d="M8 21h8M12 17v4" />
      </svg>
    ),
    title: 'Enterprise Ready',
    desc: 'PostgreSQL, Redis, Celery, Prometheus/Grafana, horizontal scaling, and COPPA/FERPA audit trails. Built for school districts and enterprises, not just home deployments.',
    accent: false,
  },
  {
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
        <path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
    ),
    title: 'Encrypted Everything',
    desc: 'AES-256 at rest via SQLCipher, TLS 1.3 in transit, Argon2id password hashing. PII is never stored in plaintext. COPPA, FERPA, and GDPR endpoints included.',
    accent: false,
  },
]

const cardVariants: Variants = {
  hidden: { opacity: 0, y: 32, scale: 0.95, filter: 'blur(8px)' },
  visible: (i: number) => ({
    opacity: 1,
    y: 0,
    scale: 1,
    filter: 'blur(0px)',
    transition: { duration: 0.5, delay: i * 0.08, ease: [0.25, 0.1, 0.25, 1] },
  }),
}

export default function Features() {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '-80px' })
  const prefersReducedMotion = useReducedMotion()

  return (
    <section
      id="features"
      className="relative py-32 overflow-hidden"
      aria-labelledby="features-heading"
    >
      {/* Subtle background glow */}
      <div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[800px] h-[400px] opacity-[0.04] pointer-events-none"
        style={{
          background: 'radial-gradient(ellipse, #f59e0b 0%, transparent 70%)',
          filter: 'blur(60px)',
        }}
        aria-hidden="true"
      />

      <div className="relative z-10 max-w-6xl mx-auto px-6 sm:px-8 lg:px-12 xl:px-16">
        {/* Section header */}
        <div className="text-center mb-16 max-w-2xl mx-auto">
          <motion.p
            initial={prefersReducedMotion ? false : { opacity: 0, y: 10 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.4 }}
            className="text-xs font-bold uppercase tracking-[0.15em] text-amber-500 mb-4"
          >
            Why snflwr.ai?
          </motion.p>
          <motion.h2
            id="features-heading"
            initial={prefersReducedMotion ? false : { opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: 0.1 }}
            className="text-4xl md:text-5xl font-black tracking-tight text-white mb-5"
          >
            Everything you need for safe AI in K-12
          </motion.h2>
          <motion.p
            initial={prefersReducedMotion ? false : { opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.4, delay: 0.2 }}
            className="text-white/50 leading-relaxed"
          >
            Designed for educators, parents, and school districts who need real
            safety guarantees — not just content policies that can be edited in a browser.
          </motion.p>
        </div>

        {/* Grid */}
        <ul
          ref={ref}
          role="list"
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
        >
          {features.map((f, i) => (
            <motion.li
              key={f.title}
              custom={i}
              initial={prefersReducedMotion ? false : 'hidden'}
              animate={prefersReducedMotion ? 'visible' : (inView ? 'visible' : 'hidden')}
              variants={cardVariants}
              whileHover={prefersReducedMotion ? {} : { y: -4, scale: 1.01 }}
              transition={{ type: 'spring', stiffness: 300, damping: 20 }}
              className={[
                'group relative p-7 rounded-2xl border transition-colors duration-300 cursor-default',
                f.accent
                  ? 'border-amber-500/30 bg-gradient-to-br from-amber-500/10 to-emerald-500/5'
                  : 'border-white/[0.07] bg-white/[0.03] hover:bg-white/[0.06] hover:border-white/[0.12]',
              ].join(' ')}
            >
              {/* Glow on hover for accent card */}
              {f.accent && (
                <div
                  className="absolute inset-0 rounded-2xl opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
                  style={{
                    background:
                      'radial-gradient(circle at 50% 0%, rgba(251,191,36,0.12) 0%, transparent 70%)',
                  }}
                  aria-hidden="true"
                />
              )}

              <div
                className={[
                  'w-11 h-11 rounded-xl flex items-center justify-center mb-5',
                  f.accent
                    ? 'bg-amber-500/20 text-amber-400'
                    : 'bg-white/[0.06] text-white/60 group-hover:text-white/80 transition-colors',
                ].join(' ')}
                aria-hidden="true"
              >
                {f.icon}
              </div>

              <h3 className="text-base font-bold text-white mb-2 tracking-tight">
                {f.title}
              </h3>
              <p className="text-sm text-white/50 leading-relaxed">{f.desc}</p>
            </motion.li>
          ))}
        </ul>
      </div>
    </section>
  )
}
