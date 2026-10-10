# Updating GitHub and redeploying on Railway (Android, no computer needed)

## A. Put the new files on GitHub (from your phone)

Easiest: GitHub's website in Chrome, using "Desktop site".

1. Unzip `smartassist-updated.zip` on your phone (Files by Google > tap the zip > Extract).
2. Open Chrome > github.com > sign in > open your SmartAssist repository.
3. Chrome menu (three dots) > tick **Desktop site**.
4. Your repo's files may sit at the top level or inside a `smartassist/` folder. Match that: the new
   `Dockerfile`, `requirements.txt` and `railway.json` must end up in the same folder as the existing `Dockerfile`.
5. Tap **Add file > Upload files**, choose the extracted files and folders, and upload them over the
   old ones (same names overwrite). Upload folder contents for: `app`, `frontend`, `scripts`, `tests`,
   `docs`, `knowledge_base`. Also upload `Dockerfile`, `railway.json`, `requirements.txt`,
   `requirements-dev.txt`, `.env.example`, `README.md`, `RAILWAY_UPDATE_STEPS.md`.
6. Do NOT upload any `.env` file. Your real key goes only into Railway (step B).
7. Write a commit message such as "Fix escalation loop, migrate Gemini SDK, speed up" and tap
   **Commit changes** (to your main/deployed branch).

Alternative: the **GitHub mobile app** can't upload folders, so use Chrome as above. If you have Termux,
`git add -A && git commit -m "..." && git push` works too.

## B. Set Railway variables (once)

1. Open railway.com (or the Railway app) > your project > the SmartAssist service > **Variables**.
2. Make sure these exist:
   - `GEMINI_API_KEY` = your real key from https://aistudio.google.com/app/apikey
   - `ADMIN_USERNAME` and `ADMIN_PASSWORD_HASH` (keep what you already have)
3. Optional: `GEMINI_MODEL=gemini-2.5-flash-lite` for a faster, cheaper model.
4. Do NOT set `PORT` yourself; Railway provides it.

## C. Redeploy

1. Railway usually redeploys automatically after the GitHub commit. If not: service > **Deployments** >
   three dots on the latest > **Redeploy**.
2. The first build is slow (several minutes): it installs PyTorch (CPU) and pre-downloads the embedding
   model. Wait for **Active**/**Success**. If it fails, open **Build Logs** and send me the last 30 lines.
3. Open **Deploy Logs**. Within about a minute of starting you should see a line like
   `{"event": "warmup_done", "embedder_ready": true, "articles_indexed": 31, ...}`.
   `embedder_ready: false` means the embedding model failed to load (see the logs).

## D. Test the live app (only now can anything be called "fixed")

1. Open `https://smartassist-ai-powered-customer-support-chatbot-production.up.railway.app/app/`
2. Try, in a new chat: "I forgot my password", "My order is late", "What is the capital of France?", "hello",
   and "talk to a human". The first three should get real answers; the last should say it can't transfer you
   and has not notified anyone.
3. Each answer produces a log line `{"event": "chat_timing", ...}` in Deploy Logs showing `llm_wait` (Gemini
   time) vs `local` (our code) vs `total`.
4. If answers mention "isn't set up", `GEMINI_API_KEY` is missing or wrong in Railway Variables.
   If answers say "trouble generating a response", check the logs for the `fallback_reason`.

## E. Optional: keep chat history across redeploys

Railway containers lose local files on every redeploy. To keep `conversations.db` and `admin.db`:
service > **Settings > Volumes > Add Volume**, mount path `/data`, then add variable `DATA_DIR=/data`.
