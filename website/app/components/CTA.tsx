'use client'

import { motion, useReducedMotion } from 'framer-motion'

export default function CTA() {
  const prefersReducedMotion = useReducedMotion()

  return (
    <section
      id="safety"
      className="relative py-32 overflow-hidden"
      aria-labelledby="cta-heading"
    >
      {/* Background glows */}
      <div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[400px] pointer-events-none opacity-[0.08]"
        style={{
          background:
            'radial-gradient(ellipse, #f59e0b 0%, #10b981 50%, transparent 75%)',
          filter: 'blur(80px)',
        }}
        aria-hidden="true"
      />

      <div className="relative z-10 max-w-2xl mx-auto px-6 text-center">
        <motion.div
          initial={prefersReducedMotion ? false : { opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.6, ease: 'easeOut' }}
          className="relative p-px rounded-3xl"
          style={{
            background: 'linear-gradient(135deg, rgba(251,191,36,0.4) 0%, rgba(16,185,129,0.4) 50%, rgba(251,191,36,0.2) 100%)',
          }}
        >
          {/* Pulsing border glow */}
          <motion.div
            className="absolute inset-0 rounded-3xl pointer-events-none"
            animate={prefersReducedMotion ? { opacity: 0.75 } : { opacity: [0.6, 1, 0.6] }}
            transition={prefersReducedMotion ? {} : { duration: 1.5, repeat: Infinity, ease: 'easeInOut' }}
            style={{
              background: 'linear-gradient(135deg, rgba(251,191,36,0.3) 0%, rgba(16,185,129,0.3) 100%)',
              filter: 'blur(12px)',
            }}
            aria-hidden="true"
          />

          <div className="relative rounded-[calc(1.5rem-1px)] bg-[#0d0d0f] px-10 py-14">
            <motion.p
              initial={prefersReducedMotion ? false : { opacity: 0, y: 8 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.4, delay: 0.1 }}
              className="text-xs font-bold uppercase tracking-[0.15em] text-amber-500 mb-5"
            >
              Open Source · AGPL-3.0
            </motion.p>

            <motion.h2
              id="cta-heading"
              initial={prefersReducedMotion ? false : { opacity: 0, y: 14 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: 0.15 }}
              className="text-4xl md:text-5xl font-black tracking-tighter text-white mb-5"
            >
              Ready to deploy safe AI?
            </motion.h2>

            <motion.p
              initial={prefersReducedMotion ? false : { opacity: 0, y: 10 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.4, delay: 0.25 }}
              className="text-white/50 mb-10 leading-relaxed"
            >
              8 GB RAM recommended · 10 GB free disk · Docker Desktop
            </motion.p>

            <motion.div
              initial={prefersReducedMotion ? false : { opacity: 0, y: 10 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.4, delay: 0.35 }}
              className="flex flex-col sm:flex-row gap-3 justify-center"
            >
              <a
                href="https://github.com/snflwr-ai/snflwr.ai"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-full bg-amber-500 hover:bg-amber-400 text-black font-bold text-sm transition-all duration-200 hover:shadow-[0_0_32px_rgba(251,191,36,0.5)] hover:-translate-y-0.5"
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
                Get started on GitHub
              </a>
              <a
                href="https://snflwr-ai.github.io/snflwr.ai/"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center justify-center gap-2 px-7 py-3.5 rounded-full border border-white/15 text-white/80 hover:text-white hover:border-white/30 font-semibold text-sm transition-all duration-200 hover:bg-white/[0.04]"
              >
                Read the docs
              </a>
            </motion.div>
          </div>
        </motion.div>
      </div>
    </section>
  )
}
