'use client'

import { motion, useReducedMotion } from 'framer-motion'

const stats = [
  { num: '3,080+', label: 'Tests' },
  { num: '88%', label: 'Coverage' },
  { num: '5-stage', label: 'Safety pipeline' },
  { num: '100%', label: 'Offline capable' },
]

export default function Hero() {
  const prefersReducedMotion = useReducedMotion()

  return (
    <section
      className="relative min-h-screen flex items-center justify-center overflow-hidden pt-16"
      aria-labelledby="hero-headline"
    >
      {/* Animated gradient mesh background */}
      <div className="absolute inset-0 pointer-events-none" aria-hidden="true">
        <div
          className="absolute top-1/4 left-1/4 w-[600px] h-[600px] rounded-full opacity-20"
          style={{
            background: 'radial-gradient(circle, #f59e0b 0%, transparent 70%)',
            filter: 'blur(80px)',
            animation: prefersReducedMotion ? 'none' : 'float-slow 12s ease-in-out infinite',
          }}
        />
        <div
          className="absolute top-1/2 right-1/4 w-[500px] h-[500px] rounded-full opacity-15"
          style={{
            background: 'radial-gradient(circle, #10b981 0%, transparent 70%)',
            filter: 'blur(80px)',
            animation: prefersReducedMotion ? 'none' : 'float-medium 10s ease-in-out infinite',
          }}
        />
        <div
          className="absolute bottom-1/4 left-1/2 w-[400px] h-[400px] rounded-full opacity-10"
          style={{
            background: 'radial-gradient(circle, #f59e0b 0%, transparent 70%)',
            filter: 'blur(100px)',
            animation: prefersReducedMotion ? 'none' : 'float-slow 15s ease-in-out infinite reverse',
          }}
        />
        {/* Grid overlay */}
        <div
          className="absolute inset-0 opacity-[0.03]"
          style={{
            backgroundImage:
              'linear-gradient(rgba(255,255,255,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.1) 1px, transparent 1px)',
            backgroundSize: '64px 64px',
          }}
        />
      </div>

      <div className="relative z-10 max-w-4xl mx-auto px-6 text-center">
        {/* Badge */}
        <motion.div
          initial={prefersReducedMotion ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: 0.1 }}
          className="inline-flex items-center gap-2 mb-8 px-4 py-1.5 rounded-full text-xs font-semibold tracking-wide text-amber-400 border border-amber-500/25 bg-amber-500/10 backdrop-blur-sm"
        >
          <span
            className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse"
            aria-hidden="true"
          />
          K-12 Safe AI Learning Platform
        </motion.div>

        {/* Headline */}
        <motion.h1
          id="hero-headline"
          initial={prefersReducedMotion ? false : { opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.6, delay: 0.2 }}
          className="text-5xl md:text-6xl lg:text-7xl font-black tracking-tighter leading-[1.05] mb-6"
        >
          Safe AI for every
          <br />
          <span
            className="bg-gradient-to-r from-amber-400 to-emerald-400 bg-clip-text text-transparent"
          >
            classroom and home
          </span>
        </motion.h1>

        {/* Tagline */}
        <motion.p
          initial={prefersReducedMotion ? false : { opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: 0.35 }}
          className="text-xl md:text-2xl text-white/50 font-medium italic mb-4"
        >
          Your child talks to AI. You control what it says back.
        </motion.p>

        {/* Sub-description */}
        <motion.p
          initial={prefersReducedMotion ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: 0.45 }}
          className="text-base text-white/40 leading-relaxed max-w-2xl mx-auto mb-10"
        >
          snflwr.ai wraps Open WebUI with a FastAPI backend enforcing multi-layer content
          filtering, parental oversight, and encrypted data storage. Every message passes
          through a{' '}
          <strong className="text-white/60 font-semibold">5-stage safety pipeline</strong>{' '}
          that cannot be bypassed from the frontend. Runs entirely on your hardware —
          no cloud accounts, no data leaving your network.
        </motion.p>

        {/* CTAs */}
        <motion.div
          initial={prefersReducedMotion ? false : { opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: 0.55 }}
          className="flex flex-col sm:flex-row gap-3 justify-center mb-16"
        >
          <a
            href="https://github.com/snflwr-ai/snflwr.ai"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-full bg-amber-500 hover:bg-amber-400 text-black font-bold text-sm transition-all duration-200 hover:shadow-[0_0_28px_rgba(251,191,36,0.45)] hover:-translate-y-0.5"
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="currentColor"
              aria-hidden="true"
            >
              <path d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.531 1.032 1.531 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
            </svg>
            View on GitHub
          </a>
          <a
            href="#how-it-works"
            className="inline-flex items-center justify-center gap-2 px-6 py-3 rounded-full border border-white/15 text-white/80 hover:text-white hover:border-white/30 font-semibold text-sm transition-all duration-200 hover:bg-white/[0.04]"
          >
            See how it works
          </a>
        </motion.div>

        {/* Stats bar */}
        <motion.div
          initial={prefersReducedMotion ? false : { opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: 0.7 }}
          role="list"
          aria-label="Platform statistics"
          className="flex flex-wrap justify-center gap-0 divide-x divide-white/10 bg-white/[0.03] border border-white/[0.07] rounded-2xl backdrop-blur-sm overflow-hidden"
        >
          {stats.map((stat) => (
            <div
              key={stat.label}
              role="listitem"
              className="flex flex-col items-center justify-center px-6 py-4 flex-1 min-w-[100px]"
            >
              <span className="text-xl font-extrabold text-white tracking-tight">
                {stat.num}
              </span>
              <span className="text-xs text-white/40 font-medium uppercase tracking-widest mt-0.5">
                {stat.label}
              </span>
            </div>
          ))}
        </motion.div>
      </div>

      {/* Bottom fade */}
      <div
        className="absolute bottom-0 left-0 right-0 h-32 pointer-events-none"
        style={{
          background: 'linear-gradient(to bottom, transparent, #070708)',
        }}
        aria-hidden="true"
      />
    </section>
  )
}
