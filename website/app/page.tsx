import FloatingSunflowers from './components/FloatingSunflowers'
import Nav from './components/Nav'
import Hero from './components/Hero'
import Features from './components/Features'
import HowItWorks from './components/HowItWorks'

import CTA from './components/CTA'
import Footer from './components/Footer'

export default function Home() {
  return (
    <>
      <FloatingSunflowers />
      <Nav />
      <main id="main-content">
        <Hero />
        <Features />
        <HowItWorks />

        <CTA />
      </main>
      <Footer />
    </>
  )
}
