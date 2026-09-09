/**
 * Animated hero: "From Question to Dataset" (spec 0G.1).
 *
 * Original SVG + CSS, no animation library and no external asset. It tells the
 * product story — sources converge on a search, indicators and geographies
 * resolve, the result settles into a table, and exports fall out the bottom.
 *
 * Rules the spec makes non-negotiable, all enforced here:
 *   - honours prefers-reduced-motion (via the theme engine's `motionEnabled`)
 *   - never blocks typing or search
 *   - pauses when the tab is hidden (`sed-decorative` + `data-hidden`)
 *   - inherits semantic theme colours, so it re-skins with every preset
 */

import { useTheme } from '../contexts/ThemeContext'
import './HeroAnimation.css'

const SOURCES = ['World Bank', 'IMF', 'FRED', 'Eurostat', 'OECD', 'BIS']
const INDICATORS = ['GDP', 'Inflation', 'Employment', 'Trade', 'Debt', 'Rates']
const GEOGRAPHIES = ['Algeria', 'Maghreb', 'MENA', 'OECD']
const OUTPUTS = ['Excel', 'CSV', 'HTML', 'Profile']

export function HeroAnimation() {
  const { motionEnabled } = useTheme()

  return (
    <figure
      className={`hero-anim ${motionEnabled ? 'is-animated' : 'is-static'}`}
      aria-hidden="true"
    >
      {/* Stage 1 — official sources */}
      <div className="hero-anim__row hero-anim__sources">
        {SOURCES.map((source, i) => (
          <span
            key={source}
            className="hero-anim__chip hero-anim__chip--source sed-decorative"
            style={{ '--i': i } as React.CSSProperties}
          >
            {source}
          </span>
        ))}
      </div>

      {/* Stage 2 — streams converging on the search */}
      <svg
        className="hero-anim__streams"
        viewBox="0 0 600 70"
        preserveAspectRatio="none"
        focusable="false"
      >
        {[70, 170, 250, 350, 430, 530].map((x, i) => (
          <path
            key={x}
            className="hero-anim__stream sed-decorative"
            style={{ '--i': i } as React.CSSProperties}
            d={`M${x} 0 C ${x} 34, 300 30, 300 66`}
            fill="none"
            stroke="var(--secondary)"
            strokeWidth="1.5"
            strokeLinecap="round"
          />
        ))}
      </svg>

      {/* Stage 3 — the search itself, the focal point */}
      <div className="hero-anim__search sed-decorative">
        <svg viewBox="0 0 24 24" width="17" height="17" focusable="false">
          <circle
            cx="10.5"
            cy="10.5"
            r="6.5"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
          />
          <path
            d="M15.5 15.5 L21 21"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
          />
        </svg>
        <span className="hero-anim__query">
          <span className="hero-anim__typed">Inflation in Algeria 2000–2025</span>
          <span className="hero-anim__caret" />
        </span>
      </div>

      {/* Stage 4 — resolved concepts and geographies */}
      <div className="hero-anim__row hero-anim__resolved">
        {INDICATORS.map((label, i) => (
          <span
            key={label}
            className="hero-anim__chip hero-anim__chip--indicator sed-decorative"
            style={{ '--i': i } as React.CSSProperties}
          >
            {label}
          </span>
        ))}
        {GEOGRAPHIES.map((label, i) => (
          <span
            key={label}
            className="hero-anim__chip hero-anim__chip--geo sed-decorative"
            style={{ '--i': i + INDICATORS.length } as React.CSSProperties}
          >
            {label}
          </span>
        ))}
      </div>

      {/* Stage 5 — the assembled dataset */}
      <div className="hero-anim__table sed-decorative">
        <div className="hero-anim__thead">
          <span>country</span>
          <span>year</span>
          <span>gdp_pc</span>
          <span>inflation</span>
        </div>
        {[
          ['Algeria', '2023', '5 260', '9.3'],
          ['Morocco', '2023', '3 672', '6.1'],
          ['Tunisia', '2023', '4 264', '9.3'],
        ].map((row, i) => (
          <div
            key={row[0]}
            className="hero-anim__trow sed-decorative"
            style={{ '--i': i } as React.CSSProperties}
          >
            {row.map((cell, j) => (
              <span key={j}>{cell}</span>
            ))}
          </div>
        ))}
      </div>

      {/* Stage 6 — exports */}
      <div className="hero-anim__row hero-anim__outputs">
        {OUTPUTS.map((label, i) => (
          <span
            key={label}
            className="hero-anim__chip hero-anim__chip--output sed-decorative"
            style={{ '--i': i } as React.CSSProperties}
          >
            {label}
          </span>
        ))}
      </div>

      <figcaption className="hero-anim__caption">
        Question → Discover → Select → Build Dataset → Profile → Export
      </figcaption>
    </figure>
  )
}
