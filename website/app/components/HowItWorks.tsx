'use client'

import { useRef } from 'react'
import { motion, useInView, useReducedMotion } from 'framer-motion'

const steps = [
  {
    num: '1',
    title: 'Student opens chat interface',
    desc: 'Students interact with a polished AI tutor interface powered by Open WebUI. No accounts required for students — parents manage profiles.',
    extra: null,
  },
  {
    num: '2',
    title: 'Message passes through 5-stage safety pipeline',
    desc: 'Every message is processed by: input validation → Unicode normalization → pattern matching → LLM classification → age-adaptive rules. If any stage errors, the content is blocked.',
    extra: [
      'Input validation',
      'Unicode normalization',
      'Pattern matching',
      'LLM classification',
      'Age-adaptive rules',
    ],
  },
  {
    num: '3',
    title: 'AI processes locally via Ollama',
    desc: "The message is sent to a local Ollama instance running Qwen or any compatible model. Nothing leaves your machine. GPU acceleration is auto-detected at setup.",
    extra: null,
  },
  {
    num: '4',
    title: 'Filtered, safe response returned',
    desc: 'The AI response also passes through the safety pipeline before the student sees it. Parents receive real-time alerts for any flagged content, with full conversation history in the dashboard.',
    extra: null,
  },
]

export default function HowItWorks() {
  const ref = useRef(null)
  const inView = useInView(ref, { once: true, margin: '-80px' })
  const prefersReducedMotion = useReducedMotion()

  return (
    <section
      id="how-it-works"
      className="relative py-32 overflow-hidden"
      aria-labelledby="how-heading"
    >
      {/* Subtle right-side glow */}
      <div
        className="absolute top-1/3 right-0 w-[500px] h-[500px] opacity-[0.05] pointer-events-none"
        style={{
          background: 'radial-gradient(circle, #10b981 0%, transparent 70%)',
          filter: 'blur(80px)',
        }}
        aria-hidden="true"
      />

      <div className="relative z-10 max-w-6xl mx-auto px-6">
        {/* Section header */}
        <div className="text-center mb-16 max-w-2xl mx-auto">
          <motion.p
            initial={prefersReducedMotion ? false : { opacity: 0, y: 10 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.4 }}
            className="text-xs font-bold uppercase tracking-[0.15em] text-emerald-500 mb-4"
          >
            Under the hood
          </motion.p>
          <motion.h2
            id="how-heading"
            initial={prefersReducedMotion ? false : { opacity: 0, y: 16 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: 0.1 }}
            className="text-4xl md:text-5xl font-black tracking-tight text-white mb-5"
          >
            How It Works
          </motion.h2>
          <motion.p
            initial={prefersReducedMotion ? false : { opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.4, delay: 0.2 }}
            className="text-white/50 leading-relaxed"
          >
            The safety pipeline sits between the user and the model.
            There is no path around it.
          </motion.p>
        </div>

        {/* Steps */}
        <ol
          ref={ref}
          className="relative max-w-2xl mx-auto"
          aria-label="How snflwr.ai works"
        >
          {steps.map((step, i) => (
            <motion.li
              key={step.num}
              initial={prefersReducedMotion ? false : { opacity: 0, x: -24 }}
              animate={prefersReducedMotion ? { opacity: 1, x: 0 } : (inView ? { opacity: 1, x: 0 } : { opacity: 0, x: -24 })}
              transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.5, delay: i * 0.12, ease: 'easeOut' }}
              className={[
                'relative flex gap-6',
                i < steps.length - 1 ? 'pb-12' : '',
              ].join(' ')}
            >
              {/* Vertical line connector */}
              {i < steps.length - 1 && (
                <motion.div
                  initial={prefersReducedMotion ? false : { scaleY: 0 }}
                  animate={prefersReducedMotion ? { scaleY: 1 } : (inView ? { scaleY: 1 } : { scaleY: 0 })}
                  transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.6, delay: i * 0.12 + 0.3, ease: 'easeOut' }}
                  className="absolute left-5 top-10 bottom-0 w-px origin-top"
                  style={{
                    background:
                      'linear-gradient(to bottom, rgba(251,191,36,0.5), rgba(16,185,129,0.3), transparent)',
                  }}
                  aria-hidden="true"
                />
              )}

              {/* Step number bubble */}
              <div
                className="relative z-10 flex-shrink-0 w-10 h-10 rounded-full bg-amber-500 text-black font-extrabold text-sm flex items-center justify-center shadow-[0_0_0_4px_rgba(251,191,36,0.12)]"
                aria-hidden="true"
              >
                {step.num}
              </div>

              {/* Content */}
              <div className="pt-1.5 flex-1">
                <h3 className="text-base font-bold text-white mb-2 tracking-tight">
                  {step.title}
                </h3>
                <p className="text-sm text-white/50 leading-relaxed mb-3">
                  {step.desc}
                </p>

                {step.extra && (
                  <ul
                    className="flex flex-wrap gap-2"
                    role="list"
                    aria-label="Pipeline stages"
                  >
                    {step.extra.map((stage, si) => (
                      <li
                        key={stage}
                        className="flex items-center gap-1.5 px-3 py-1 rounded-full border border-white/[0.08] bg-white/[0.03] text-xs text-white/50 font-medium"
                      >
                        <span className="text-amber-500 font-bold text-xs" aria-hidden="true">
                          {si + 1}
                        </span>
                        {stage}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </motion.li>
          ))}
        </ol>
      </div>
    </section>
  )
}
