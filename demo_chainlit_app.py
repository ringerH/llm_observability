import sys
import os
import time
import chainlit as cl

# Insert workspace root to import harness modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eval_harness.config import settings
from eval_harness.client import call_llm, LLMError
from eval_harness import sampler, logging

# 1. Fail-fast validation check on startup: exit if no API credentials exist
gemini_key = settings.GEMINI_API_KEY
openai_key = settings.OPENAI_API_KEY

if not gemini_key and not openai_key:
    logging.log_error(
        "CRITICAL CONFIGURATION ERROR: Both GEMINI_API_KEY and OPENAI_API_KEY are missing."
    )
    print(
        "CRITICAL ERROR: Please configure either GEMINI_API_KEY or OPENAI_API_KEY in .env.",
        file=sys.stderr,
    )
    sys.exit(1)


@cl.on_chat_start
async def start():
    provider = "gemini" if gemini_key else "openai"
    cl.user_session.set("provider", provider)
    await cl.Message(
        content=f"👋 **Observability Sandbox Chatbot** is live! Running on **{provider.upper()}**.\n\n"
        "Your inputs and responses are monitored. Check the console logs and dashboard for real-time telemetry."
    ).send()


@cl.on_message
async def main(message: cl.Message):
    provider = cl.user_session.get("provider")
    start_time = time.time()

    # 2. Inform user and generate response using client wrapper
    msg = cl.Message(content="")
    await msg.stream_token("Thinking...")

    try:
        # Call the real LLM (Fail-fast on exceptions)
        response_text = call_llm(message.content, provider=provider)

        # Output the response back to UI
        msg.content = response_text
        await msg.update()

        latency_ms = (time.time() - start_time) * 1000
        cost = (len(message.content) + len(response_text)) * 0.000002

        # 3. Log telemetry to the local queue database (SQLite)
        request_id = sampler.log_production_traffic(
            config_hash=f"chainlit_{provider}_v1.0",
            input_data=message.content,
            actual_output=response_text,
            latency_ms=latency_ms,
            cost=cost,
        )

        if request_id:
            logging.log_info(
                f"Chainlit app logged telemetry with Request ID: {request_id}"
            )
            # Send verification card
            await cl.Message(
                content=f"📝 **Telemetry Logged (PII Masked):**\n"
                f"* **Request ID:** `{request_id}`\n"
                f"* **Latency:** `{latency_ms:.1f} ms`\n"
                f"* Run the evaluator worker in a terminal: `python -m eval_harness.worker` to grade this event."
            ).send()
        else:
            await cl.Message(
                content="⚠️ **Observability Skipped:** Request was not sampled due to `SAMPLING_RATE` constraints."
            ).send()

    except LLMError as e:
        # Fail fast: report the specific LLM API error to the UI and exit
        msg.content = f"❌ **LLM Provider API Error (Fail-fast):**\n\n`{type(e).__name__}: {str(e)}`"
        await msg.update()
        logging.log_error(f"Chainlit LLM client failure: {e}", exc_info=e)
    except Exception as e:
        # Handle other runtime errors
        msg.content = f"❌ **Unexpected Application Error:**\n\n`{str(e)}`"
        await msg.update()
        logging.log_error(f"Unexpected application failure: {e}", exc_info=e)
