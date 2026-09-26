import { Icon } from './Icon'

/** "Hoy no hay pick": un estado honesto, con salida a los otros días. */
export function EmptyPicks({ title, body, actions }: {
  title: string
  body: string
  actions: { label: string; onClick: () => void; primary?: boolean }[]
}) {
  return (
    <section className="flex min-h-96 flex-col justify-center gap-6 rounded-3xl border border-dashed border-ink-500 bg-ink-850 p-8 sm:p-10">
      <div className="flex size-14 items-center justify-center rounded-2xl bg-ink-800 text-muted">
        <Icon name="ticketOff" size={26} />
      </div>
      <div className="flex max-w-xl flex-col gap-2.5">
        <h2 className="m-0 font-display text-3xl leading-tight font-extrabold tracking-tight sm:text-[34px]">{title}</h2>
        <p className="m-0 text-base leading-relaxed text-pretty text-muted">{body}</p>
      </div>
      {actions.length > 0 && (
        <div className="flex flex-wrap gap-3">
          {actions.map((a) => (
            <button
              key={a.label}
              type="button"
              onClick={a.onClick}
              className={`min-h-11 rounded-xl px-4.5 text-sm transition-colors ${
                a.primary ? 'bg-fg font-semibold text-ink-950 hover:bg-white' : 'border border-ink-500 font-medium text-fg-2 hover:text-fg'
              }`}
            >
              {a.label}
            </button>
          ))}
        </div>
      )}
    </section>
  )
}
