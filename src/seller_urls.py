"""Stable merchant model-catalog URLs for seller identifiers.

These links are deliberately separate from ``PriceRecord.source_url``: the
latter is the evidence/data endpoint used to obtain a price, while these URLs
take a reader to the seller's own model catalog or model documentation.
"""

from __future__ import annotations

from .records import PriceRecord


# LiteLLM seller names do not carry a merchant URL. Keep their stable model
# catalogs here; models.dev records provide the equivalent URL in their own
# provider metadata and therefore do not need to be duplicated.
SELLER_CATALOG_URLS = {
    "302ai": "https://price.302.ai/",
    "abacus": "https://abacus.ai/help/developer-platform/route-llm/",
    "ai21": "https://docs.ai21.com/docs/models",
    "aihubmix": "https://docs.aihubmix.com/en/api/Models-API",
    "amazon_nova": "https://docs.aws.amazon.com/nova/latest/userguide/models.html",
    "anthropic": "https://docs.anthropic.com/en/docs/about-claude/models/overview",
    "anyscale": "https://docs.anyscale.com/llm/serving/models",
    "azure": "https://ai.azure.com/explore/models",
    "azure_text": "https://ai.azure.com/explore/models",
    "baseten": "https://www.baseten.co/library/",
    "bedrock_mantle": "https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html",
    "cerebras": "https://inference-docs.cerebras.ai/introduction",
    "cloudflare": "https://developers.cloudflare.com/workers-ai/models/",
    "cohere": "https://docs.cohere.com/docs/models",
    "crof": "https://crof.ai/pricing",
    "dashscope": "https://www.alibabacloud.com/help/en/model-studio/models",
    "databricks": "https://docs.databricks.com/aws/en/machine-learning/model-serving/foundation-model-overview",
    "deepinfra": "https://deepinfra.com/models",
    "deepseek": "https://api-docs.deepseek.com/quick_start/pricing",
    "empiriolabs": "https://docs.empiriolabs.ai/models-pricing",
    "friendliai": "https://docs.friendli.ai/guides/serverless_endpoints/supported_models",
    "gmi": "https://docs.gmicloud.ai/inference/models",
    "gradient_ai": "https://docs.digitalocean.com/products/gradient-ai-platform/details/models/",
    "groq": "https://console.groq.com/docs/models",
    "inception": "https://platform.inceptionlabs.ai/docs/models",
    "io-net": "https://io.net/docs/guides/intelligence/exploring-ai-models",
    "jiekou": "https://docs.jiekou.ai/docs/model/llm",
    "kilo": "https://kilo.ai/docs/gateway/models-and-providers",
    "libertai": "https://docs.libertai.io/apis/text/",
    # llamagate.dev currently serves a mismatched TLS certificate. Its official
    # SDK package contains the public Available Models table and remains usable.
    "llamagate": "https://www.npmjs.com/package/@llamagate/ai-sdk-provider",
    "llmgateway": "https://docs.llmgateway.io/v1_models",
    "meganova": "https://docs.meganova.ai/inference-models/model-list",
    "merge-gateway": "https://docs.merge.dev/merge-gateway/models/catalog",
    "meta": "https://llama.developer.meta.com/docs/models",
    "minimax": "https://platform.minimax.io/docs/guides/models-intro",
    "moonshot": "https://platform.moonshot.ai/docs/pricing",
    "nano-gpt": "https://docs.nano-gpt.com/api-reference/endpoint/models",
    "novita-ai": "https://novita.ai/models",
    "nvidia": "https://build.nvidia.com/models?label=text-to-text",
    "ofox": "https://ofox.ai/docs/develop/models",
    "opencode": "https://dev.opencode.ai/docs/zen",
    "opencode-go": "https://dev.opencode.ai/docs/zen",
    "openai": "https://platform.openai.com/docs/models",
    "orcarouter": "https://docs.orcarouter.ai/getting-started/models",
    "perplexity": "https://docs.perplexity.ai/getting-started/models/models/overview",
    "pioneer": "https://agent.pioneer.ai/models",
    "poe": "https://creator.poe.com/api-reference/listModels",
    "qihang-ai": "https://www.qhaigc.net/docs/models",
    "replicate": "https://replicate.com/explore",
    "sambanova": "https://cloud.sambanova.ai/apis",
    "scaleway": "https://www.scaleway.com/en/docs/generative-apis/reference-content/supported-models/",
    "snowflake": "https://docs.snowflake.com/en/user-guide/snowflake-cortex/llm-functions#availability",
    "sap-ai-core": "https://help.sap.com/docs/sap-ai-core/generative-ai/supported-models",
    "stepfun-ai": "https://platform.stepfun.ai/docs/en/guides/models/overview",
    "submodel": "https://submodel.gitbook.io/docs/instagen/overview-1/available-models",
    "tencent": "https://www.tencentcloud.com/products/tokenhub",
    "tensormesh": "https://serverless.tensormesh.ai/",
    "text-completion-inception": "https://platform.inceptionlabs.ai/docs/models",
    "text-completion-openai": "https://platform.openai.com/docs/models",
    "together_ai": "https://www.together.ai/models",
    "vercel_ai_gateway": "https://vercel.com/ai-gateway/models",
    "wandb": "https://wandb.ai/inference/models",
    "upstage": "https://console.upstage.ai/docs/models",
    "vercel": "https://vercel.com/ai-gateway/models",
    "xai": "https://docs.x.ai/docs/models",
    "xiaomi": "https://platform.xiaomimimo.com/models",
    "xpersona": "https://www.xpersona.co/pricing",
    "zai": "https://docs.z.ai/guides/overview/pricing",
    "zenmux": "https://zenmux.ai/models",
    "azure_ai": "https://ai.azure.com/explore/models",
    "bedrock": "https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html",
    "bedrock_converse": "https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html",
    "berget": "https://docs.berget.ai/",
    "cortecs": "https://cortecs.ai/serverlessModels",
    "cohere_chat": "https://docs.cohere.com/docs/models",
    "crusoe": "https://docs.crusoecloud.com/quickstart/self-serve-deployments/index.html",
    "darkbloom": "https://console.darkbloom.dev/models",
    "fireworks_ai": "https://fireworks.ai/models",
    "gemini": "https://ai.google.dev/gemini-api/docs/models",
    "hyperbolic": "https://app.hyperbolic.xyz/models?category=text",
    "lambda_ai": "https://lambda.ai/inference-models/author/lambda/page/1",
    "mistral": "https://docs.mistral.ai/getting-started/models/",
    "nebius": "https://docs.tokenfactory.nebius.com/models/",
    "novita": "https://novita.ai/docs/api-reference/model-apis-llm-list-models",
    "nscale": "https://docs.nscale.com/docs/inference/serverless-models/current#chat-models",
    "oci": "https://docs.oracle.com/en-us/iaas/Content/generative-ai/pretrained-models.htm",
    "openrouter": "https://openrouter.ai/models",
    "ovhcloud": "https://www.ovhcloud.com/en/public-cloud/ai-endpoints/catalog/",
    "pinstripes": "https://pinstripes.io/slices/",
    "vertex_ai": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-ai21_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-anthropic_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-deepseek_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-embedding-models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-language-models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-llama_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-minimax_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-mistral_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-moonshot_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-openai_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-qwen_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "vertex_ai-zai_models": "https://console.cloud.google.com/vertex-ai/model-garden",
    "watsonx": "https://dataplatform.cloud.ibm.com/docs/content/wsj/analyze-data/fm-models.html?context=wx",
}


def catalog_url_for(seller: str) -> str:
    """Return a known seller model catalog, including composite router IDs."""
    if seller in SELLER_CATALOG_URLS:
        return SELLER_CATALOG_URLS[seller]
    # A nested router quote is accessed through the router's model catalog.
    if seller.startswith("hf_router/"):
        return "https://huggingface.co/models?inference_provider=all"
    return ""


def seller_url_of(record: PriceRecord | None) -> str:
    """Return the merchant landing page belonging to this exact quote."""
    if record is None:
        return ""
    raw = record.raw or {}
    explicit = str(raw.get("seller_url") or "")
    mapped = catalog_url_for(str(raw.get("seller") or record.provider))
    if mapped or explicit:
        # Curated catalog URLs intentionally override generic provider metadata
        # such as a documentation root or repository URL.
        return mapped or explicit
    # Unknown vendored sellers should be conspicuously blank, never mislabeled
    # with a models.dev/LiteLLM repository or raw-data URL.
    if raw.get("provider_name") in {"models.dev", "LiteLLM"}:
        return ""
    return str(raw.get("weblink") or "")
