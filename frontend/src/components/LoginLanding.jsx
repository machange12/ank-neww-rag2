import { Loader2, LogIn, ShieldCheck } from "lucide-react";

export function LoginLanding({ email, password, error, isLoggingIn, setEmail, setPassword, login }) {
  return (
    <main className="min-h-screen bg-gray-50 text-gray-900">
      <div className="grid min-h-screen lg:grid-cols-[1fr_420px]">
        <section className="flex flex-col justify-between border-r border-gray-200 bg-white px-8 py-8 lg:px-12">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-blue-600 text-sm font-semibold text-white">
              AN
            </div>
            <div>
              <div className="text-sm font-semibold uppercase tracking-wide">ANK RAG</div>
              <div className="text-xs text-gray-500">Secure legal research workspace</div>
            </div>
          </div>

          <div className="max-w-2xl py-16">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
              <ShieldCheck size={14} /> Role-aware document retrieval
            </div>
            <h1 className="font-serif text-4xl font-semibold leading-tight text-gray-950 md:text-5xl">
              Ask your firm's indexed knowledge base with access controls intact.
            </h1>
            <p className="mt-5 max-w-xl text-sm leading-6 text-gray-600">
              Sign in with your Supabase user account to open the RAG dashboard, search secure matter files, and save answers for later review.
            </p>
          </div>

          <div className="grid gap-3 text-xs text-gray-500 md:grid-cols-3">
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="mb-1 font-medium text-gray-900">JWT auth</div>
              User sessions go through the backend login endpoint.
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="mb-1 font-medium text-gray-900">RLS-aware</div>
              Chat requests use the signed-in user's token.
            </div>
            <div className="rounded-lg border border-gray-200 bg-gray-50 p-3">
              <div className="mb-1 font-medium text-gray-900">Workspace tools</div>
              History, sources, copy, export, and save controls are active.
            </div>
          </div>
        </section>

        <section className="flex items-center justify-center px-6 py-10">
          <form onSubmit={login} className="w-full max-w-sm rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <div className="mb-5">
              <h2 className="text-lg font-semibold">Sign in</h2>
              <p className="mt-1 text-xs text-gray-500">Use a Supabase Auth user for this law firm RAG.</p>
            </div>

            <label className="mb-1 block text-xs font-medium text-gray-600">Email</label>
            <input
              className="mb-3 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="partner@ak.law"
              required
            />

            <label className="mb-1 block text-xs font-medium text-gray-600">Password</label>
            <input
              className="mb-4 w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-400 focus:outline-none"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password"
              required
            />

            {error && <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

            <button
              disabled={isLoggingIn}
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-blue-600 px-3 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-gray-300"
            >
              {isLoggingIn ? <Loader2 className="animate-spin" size={16} /> : <LogIn size={16} />}
              Continue to dashboard
            </button>
          </form>
        </section>
      </div>
    </main>
  );
}
