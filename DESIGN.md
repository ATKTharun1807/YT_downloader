# Design System — YT_DOWNLOADER

## 1. Aesthetic Design Principles

The design of **YT_DOWNLOADER** follows a **Modern Dark Glassmorphism** aesthetic. It prioritizes high visual contrast, responsiveness, fluid micro-animations, and minimal noise to provide a premium user experience.

Key principles:
- **Depth & Translucency**: Layered frosted glass panels (`backdrop-filter: blur(16px)`) with subtle light borders (`rgba(255, 255, 255, 0.1)`).
- **Vibrant Accents**: High-contrast linear gradients transitioning from Electric Indigo to Violet and Magenta for primary call-to-actions.
- **Micro-Interactions**: Smooth scale transforms, glow focus effects, and animated progress fill bars.
- **Clarity & Focus**: Minimalist layout centered around a prominent URL input bar and real-time status card.

---

## 2. Color Palette

```
  Dark Slate Base        Glass Surface          Primary Gradient           Accent Pink
 ┌───────────────┐      ┌───────────────┐      ┌─────────────────┐      ┌───────────────┐
 │   #0f172a     │      │ rgba(30,41,59)│      │ #6366f1 → #8b5cf6│     │   #ec4899     │
 └───────────────┘      └───────────────┘      └─────────────────┘      └───────────────┘
```

| Token | Hex / Value | Usage |
|---|---|---|
| `--bg-dark` | `#0f172a` | Deep slate page background |
| `--surface-glass` | `rgba(30, 41, 59, 0.7)` | Translucent card container fill |
| `--surface-border` | `rgba(255, 255, 255, 0.1)` | Crisp glass border stroke |
| `--primary-indigo` | `#6366f1` | Primary action button start |
| `--primary-purple` | `#8b5cf6` | Primary action button end |
| `--accent-pink` | `#ec4899` | Secondary accent & highlight tags |
| `--status-success` | `#22c55e` | Download complete indicator |
| `--status-warning` | `#eab308` | Active download / progress indicator |
| `--status-error` | `#ef4444` | Invalid URL / error alert card |
| `--text-primary` | `#f8fafc` | Main headings & primary body text |
| `--text-secondary` | `#94a3b8` | Subtitles, labels, and muted text |

---

## 3. Typography & Hierarchy

- **Font Family**: `-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Inter, sans-serif`
- **Type Scale**:
  - `Display / H1`: `2.25rem (36px)`, weight `700`, line-height `1.2`
  - `Section / H2`: `1.5rem (24px)`, weight `600`, line-height `1.3`
  - `Subheading / H3`: `1.125rem (18px)`, weight `600`, line-height `1.4`
  - `Body Standard`: `1.0rem (16px)`, weight `400`, line-height `1.5`
  - `Caption / Micro`: `0.875rem (14px)`, weight `500`, line-height `1.4`

---

## 4. UI Components Specifications

### 4.1 URL Search / Analyze Input
- **Structure**: High-profile input bar with integrated analyze button.
- **Default State**: Glass fill `rgba(15, 23, 42, 0.6)`, border `1px solid rgba(255, 255, 255, 0.1)`.
- **Focus State**: Indigo glow outline `box-shadow: 0 0 0 3px rgba(99, 102, 241, 0.35)`.

### 4.2 Action Buttons
- **Primary Download Button**: Linear gradient `#6366f1` to `#8b5cf6`, rounded-xl `0.75rem`, subtle drop shadow.
- **Hover State**: `transform: translateY(-2px)`, increased glow shadow.

### 4.3 Resolution Quality Pills
- **Tags**: Badges for 4K, 1080p, 720p, MP3.
- **Active State**: Selected option highlighted in gradient background with white check icon.

### 4.4 Animated Progress Bar
- **Track**: Semi-transparent dark track `rgba(255, 255, 255, 0.05)`.
- **Fill**: Gradient stripe animated with smooth CSS transitions (`transition: width 0.3s ease`).
