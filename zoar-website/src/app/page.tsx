export default function Home() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center bg-white font-sans dark:bg-zinc-950">
      <main className="flex max-w-2xl flex-col items-center gap-8 px-8 py-24 text-center">
        <h1 className="text-5xl font-bold tracking-tight text-zinc-900 dark:text-white">
          Zoar Bathroom Rentals
        </h1>
        <p className="text-xl text-zinc-600 dark:text-zinc-400">
          Premium portable restrooms for weddings, events, and construction
          sites across Los Angeles and Southern California.
        </p>
        <div className="flex gap-4">
          <a
            href="/quote"
            className="rounded-full bg-zinc-900 px-8 py-3 text-sm font-medium text-white transition-colors hover:bg-zinc-700 dark:bg-white dark:text-zinc-900 dark:hover:bg-zinc-200"
          >
            Get a Quote
          </a>
          <a
            href="/about"
            className="rounded-full border border-zinc-300 px-8 py-3 text-sm font-medium text-zinc-900 transition-colors hover:bg-zinc-50 dark:border-zinc-700 dark:text-white dark:hover:bg-zinc-900"
          >
            Learn More
          </a>
        </div>
      </main>
    </div>
  );
}
