## Executive Summary

Chain-of-thought (CoT) prompting has emerged as a significant technique in the evolution of language models, enabling step-by-step reasoning that mirrors human problem-solving. Since its formal introduction in 2022, CoT prompting has seen rapid adoption and refinement, with major milestones including its integration into the reasoning modes of leading models and the development of advanced variants such as GAN-CoT and Select-Prompt. Evidence generally supports CoT's effectiveness in improving performance on complex, multi-step reasoning tasks, particularly in large-scale models. However, researchers remain divided on its universal applicability, computational costs, and benefits for simpler tasks or highly capable models. Key disagreements center on the magnitude of performance gains, the trade-off between interpretability and efficiency, and the contexts in which CoT prompting is most advantageous. While consensus exists regarding its value for transparency and interpretability, the field continues to debate its optimal use cases and limitations.

## Findings

### Major Milestones in the Development of Chain-of-Thought Prompting

In 2026, chain-of-thought prompting is built into the reasoning modes of models like GPT-5, Claude Opus 4.7, Gemini 3 Pro, and DeepSeek R1 [confidence: 0.65 · supported · Source: Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1]. The APIs of closed models in 2026 expose chain-of-thought reasoning through a controllable budget or effort parameter [confidence: 0.65 · supported · Source: Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1]. DeepSeek R1 reasons by default using chain-of-thought prompting [confidence: 0.65 · supported · Source: Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1]. By 2026, the focus of chain-of-thought prompting has shifted from teaching the model to think to deciding when to spend reasoning resources [confidence: 0.65 · supported · Source: Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1].

Four main techniques for chain-of-thought prompting in 2026 are Zero Shot, Few Shot, Self Consistency, and Reasoning Budget [confidence: 0.65 · supported · Source: Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1]. GAN-CoT, introduced in 2026, iteratively refines chain-of-thought templates via generative adversarial training [confidence: 0.65 · supported · Source: Hierarchical Chain-of-Thought Prompting: Enhancing LLM ...]. Select-Prompt, developed in 2026, improves reasoning by mining hard samples and optimizing prompts through selecting correct reasoning chains from multiple generated candidates [confidence: 0.65 · supported · Source: Hierarchical Chain-of-Thought Prompting: Enhancing LLM ...].

Lack of structure at the step level in chain-of-thought prompting can lead to redundant and poorly organized reasoning [confidence: 0.65 · supported · Source: Hierarchical Chain-of-Thought Prompting: Enhancing LLM ...]. Longer reasoning traces in chain-of-thought prompting do not necessarily imply better reasoning and can reflect disorganized exploration [confidence: 0.65 · supported · Source: Hierarchical Chain-of-Thought Prompting: Enhancing LLM ...]. Evidence suggests that the computational overhead of transformer models increases with the number of reasoning steps in chain-of-thought prompting [confidence: 0.40 · insufficient · Source: Hierarchical Chain-of-Thought Prompting: Enhancing LLM ...].

A simple modification to prompt language models to generate intermediate reasoning steps before a final answer has led to notable gains on multi-step reasoning tasks [confidence: 0.65 · supported · Source: Hierarchical Chain-of-Thought Prompting: Enhancing LLM ...]. Chain-of-thought prompting is a prompt engineering technique that enhances the output of large language models for complex tasks involving multistep reasoning [confidence: 0.65 · supported · Source: What is chain of thought (CoT) prompting?]. Chain-of-thought prompting guides language models through a step-by-step reasoning process using a coherent series of logical steps [confidence: 0.65 · supported · Source: What is chain of thought (CoT) prompting?]. Evidence suggests that chain-of-thought prompting uses exemplar-based prompts to illustrate the reasoning process and enhance the model’s ability to generate analogous reasoning chains for novel tasks [confidence: 0.40 · insufficient · Source: What is chain of thought (CoT) prompting?].

### First Introduction and Formalization of Chain-of-Thought Prompting

Chain-of-Thought (CoT) prompting is a technique that instructs models to 'think step by step' before answering, mirroring human problem-solving by breaking down complex tasks [confidence: 0.80 · supported · Source: The Decreasing Value of Chain of Thought in Prompting; Chain of Thought Prompting Guide; What is chain of thought (CoT) prompting?]. Chain-of-Thought prompting was introduced by Wei et al. (2022) [confidence: 0.72 · supported · Source: The Decreasing Value of Chain of Thought in Prompting; Chain of Thought Prompting Guide]. Evidence suggests that the original research paper introducing Chain-of-Thought prompting is titled 'Chain of Thought Prompting: Reasoning in LLMs' and was published by Google in 2022 [confidence: 0.55 · supported · Source: Chain of Thought Prompting Guide].

Evidence suggests that the 2022 paper by Wei et al. demonstrated that performance gains from Chain-of-Thought prompting only occurred once model sizes were in the billions of parameters [confidence: 0.40 · insufficient · Source: Chain of Thought Prompting Guide]. Chain-of-Thought prompting elicits reasoning in large language models by prompting them to generate intermediate reasoning steps, significantly boosting their ability to solve multistep problems such as arithmetic, common sense, and symbolic reasoning [confidence: 0.65 · supported · Source: What is chain of thought (CoT) prompting?]. Evidence suggests that Chain-of-Thought prompting enhances transparency and interpretability in large language models by elucidating intermediate reasoning steps [confidence: 0.55 · supported · Source: What is chain of thought (CoT) prompting?].

### Evidence Supporting Effectiveness of Chain-of-Thought Prompting

Chain-of-thought prompting can improve performance on various reasoning tasks in large language models [confidence: 0.65 · supported · Source: Language Models Perform Reasoning via Chain of Thought]. Successful chain-of-thought reasoning is an emergent property of model scale in large language models [confidence: 0.65 · supported · Source: Language Models Perform Reasoning via Chain of Thought]. Evidence suggests that taking the majority vote of a broad set of generated reasoning processes using chain-of-thought prompting results in 74% accuracy on the GSM8K dataset [confidence: 0.40 · insufficient · Source: Language Models Perform Reasoning via Chain of Thought]. Evidence suggests that for CommonsenseQA, StrategyQA, and Date Understanding, performance improved with chain-of-thought prompting [confidence: 0.40 · insufficient · Source: Language Models Perform Reasoning via Chain of Thought].

Adding reasoning examples to prompts dramatically improved performance on tasks requiring arithmetic, commonsense reasoning, and symbolic manipulation [confidence: 0.65 · supported · Source: Chain-of-Thought Prompting: A Guide for LLM Apps and Agents]. Chain-of-thought prompting increased accuracy to 74.4 percent on a benchmark task [confidence: 0.65 · supported · Source: Chain-of-Thought Prompting: A Guide for LLM Apps and Agents]. Evidence suggests that chain-of-thought prompting has significantly improved the reasoning capabilities of large language models [confidence: 0.45 · supported · Source: Hierarchical Chain-of-Thought Prompting: Enhancing LLM ...].

### Criticisms and Limitations of Chain-of-Thought Prompting

Ye & Durrett (2022) found that earlier LLMs such as GPT-3 and OPT may generate unreliable explanations in few-shot textual reasoning scenarios when using chain-of-thought (CoT) prompting [confidence: 0.65 · supported · Source: The Curse of CoT: On the Limitations of Chain-of-Thought in In-Context Learning]. Stechly et al. (2025) highlighted that chain-of-thought prompting relies on problem-specific prompts and has limited scalability in planning tasks [confidence: 0.65 · supported · Source: The Curse of CoT: On the Limitations of Chain-of-Thought in In-Context Learning]. Evidence suggests that chain-of-thought prompting faces inherent limitations due to the complexity of navigating the prompt and answer spaces [confidence: 0.40 · insufficient · Source: The Curse of CoT: On the Limitations of Chain-of-Thought in In-Context Learning].

The effectiveness of chain-of-thought prompting is highly reliant on the quality of the prompts provided, requiring carefully crafted examples [confidence: 0.65 · supported · Source: What is chain of thought (CoT) prompting?]. Chain-of-thought prompting requires more computational power and time compared to standard prompting due to the generation and processing of multiple reasoning steps [confidence: 0.65 · supported · Source: What is chain of thought (CoT) prompting?]. Chain-of-thought prompting is susceptible to adversarial attacks [confidence: 0.65 · supported · Source: What is chain of thought (CoT) prompting?]. Evaluating qualitative improvements in reasoning

---

## Claim Confidence Index

_Generated directly from the system's internal state, not written by the report model — every claim the report draws on, with the confidence the system actually assigned it._


**What are the major milestones in the development of chain-of-thought prompting techniques for language models?**

| Confidence | Status | Claim | Sources |
|---|---|---|---|
| 0.65 | supported | In 2026, chain-of-thought prompting is built into the reasoning modes of models like GPT-5, Claude Opus 4.7, Gemini 3 Pro, and DeepSeek R1. | Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1 |
| 0.65 | supported | The APIs of closed models in 2026 expose chain-of-thought reasoning through a controllable budget or effort parameter. | Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1 |
| 0.65 | supported | DeepSeek R1 reasons by default using chain-of-thought prompting. | Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1 |
| 0.65 | supported | By 2026, the focus of chain-of-thought prompting has shifted from teaching the model to think to deciding when to spend reasoning resources. | Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1 |
| 0.65 | supported | Four main techniques for chain-of-thought prompting in 2026 are Zero Shot, Few Shot, Self Consistency, and Reasoning Budget. | Chain of Thought Prompting 2026: GPT-5, Claude 4.7, R1 |
| 0.65 | supported | GAN-CoT, introduced in 2026, iteratively refines chain-of-thought templates via generative adversarial training. | Hierarchical Chain-of-Thought Prompting: Enhancing LLM ... |
| 0.65 | supported | Select-Prompt, developed in 2026, improves reasoning by mining hard samples and optimizing prompts through selecting correct reasoning chains from multiple generated candidates. | Hierarchical Chain-of-Thought Prompting: Enhancing LLM ... |
| 0.65 | supported | Lack of structure at the step level in chain-of-thought prompting can lead to redundant and poorly organized reasoning. | Hierarchical Chain-of-Thought Prompting: Enhancing LLM ... |
| 0.65 | supported | Longer reasoning traces in chain-of-thought prompting do not necessarily imply better reasoning and can reflect disorganized exploration. | Hierarchical Chain-of-Thought Prompting: Enhancing LLM ... |
| 0.65 | supported | A simple modification to prompt language models to generate intermediate reasoning steps before a final answer has led to notable gains on multi-step reasoning tasks. | Hierarchical Chain-of-Thought Prompting: Enhancing LLM ... |
| 0.65 | supported | Chain-of-thought prompting is a prompt engineering technique that enhances the output of large language models for complex tasks involving multistep reasoning. | What is chain of thought (CoT) prompting? |
| 0.65 | supported | Chain-of-thought prompting guides language models through a step-by-step reasoning process using a coherent series of logical steps. | What is chain of thought (CoT) prompting? |
| 0.40 | insufficient | The computational overhead of transformer models increases with the number of reasoning steps in chain-of-thought prompting. | Hierarchical Chain-of-Thought Prompting: Enhancing LLM ... |
| 0.40 | insufficient | Chain-of-thought prompting uses exemplar-based prompts to illustrate the reasoning process and enhance the model’s ability to generate analogous reasoning chains for novel tasks. | What is chain of thought (CoT) prompting? |

**Which research papers or studies first introduced and formalized chain-of-thought prompting?**

| Confidence | Status | Claim | Sources |
|---|---|---|---|
| 0.80 | supported | Chain-of-Thought (CoT) prompting is a technique that instructs models to 'think step by step' before answering, mirroring human problem-solving by breaking down complex tasks. | The Decreasing Value of Chain of Thought in Prompting; Chain of Thought Prompting Guide; What is chain of thought (CoT) prompting? |
| 0.72 | supported | Chain-of-Thought prompting was introduced by Wei et al. (2022). | The Decreasing Value of Chain of Thought in Prompting; Chain of Thought Prompting Guide |
| 0.65 | supported | Chain-of-Thought prompting elicits reasoning in large language models by prompting them to generate intermediate reasoning steps, significantly boosting their ability to solve multistep problems such as arithmetic, common sense, and symbolic reasoning. | What is chain of thought (CoT) prompting? |
| 0.55 | supported | The original research paper introducing Chain-of-Thought prompting is titled 'Chain of Thought Prompting: Reasoning in LLMs' and was published by Google in 2022. | Chain of Thought Prompting Guide |
| 0.55 | supported | Chain-of-Thought prompting enhances transparency and interpretability in large language models by elucidating intermediate reasoning steps. | What is chain of thought (CoT) prompting? |
| 0.40 | insufficient | The 2022 paper by Wei et al. demonstrated that performance gains from Chain-of-Thought prompting only occurred once model sizes were in the billions of parameters. | Chain of Thought Prompting Guide |

**What evidence has been presented in support of the effectiveness of chain-of-thought prompting in improving model reasoning or performance?**

| Confidence | Status | Claim | Sources |
|---|---|---|---|
| 0.65 | supported | Chain-of-thought prompting can improve performance on various reasoning tasks in large language models. | Language Models Perform Reasoning via Chain of Thought |
| 0.65 | supported | Successful chain-of-thought reasoning is an emergent property of model scale in large language models. | Language Models Perform Reasoning via Chain of Thought |
| 0.65 | supported | Adding reasoning examples to prompts dramatically improved performance on tasks requiring arithmetic, commonsense reasoning, and symbolic manipulation. | Chain-of-Thought Prompting: A Guide for LLM Apps and Agents |
| 0.65 | supported | Chain-of-thought prompting increased accuracy to 74.4 percent on a benchmark task. | Chain-of-Thought Prompting: A Guide for LLM Apps and Agents |
| 0.45 | supported | Chain-of-thought prompting has significantly improved the reasoning capabilities of large language models. | Hierarchical Chain-of-Thought Prompting: Enhancing LLM ... |
| 0.40 | insufficient | Taking the majority vote of a broad set of generated reasoning processes using chain-of-thought prompting results in 74% accuracy on the GSM8K dataset. | Language Models Perform Reasoning via Chain of Thought |
| 0.40 | insufficient | For CommonsenseQA, StrategyQA, and Date Understanding, performance improved with chain-of-thought prompting. | Language Models Perform Reasoning via Chain of Thought |

**What criticisms or limitations of chain-of-thought prompting have been raised in the literature, and which researchers or studies have expressed skepticism about its effectiveness?**

| Confidence | Status | Claim | Sources |
|---|---|---|---|
| 0.65 | supported | Ye & Durrett (2022) found that earlier LLMs such as GPT-3 and OPT may generate unreliable explanations in few-shot textual reasoning scenarios when using chain-of-thought (CoT) prompting. | The Curse of CoT: On the Limitations of Chain-of-Thought in  |
| 0.65 | supported | Stechly et al. (2025) highlighted that chain-of-thought prompting relies on problem-specific prompts and has limited scalability in planning tasks. | The Curse of CoT: On the Limitations of Chain-of-Thought in  |
| 0.65 | supported | The effectiveness of chain-of-thought prompting is highly reliant on the quality of the prompts provided, requiring carefully crafted examples. | What is chain of thought (CoT) prompting? |
| 0.65 | supported | Chain-of-thought prompting requires more computational power and time compared to standard prompting due to the generation and processing of multiple reasoning steps. | What is chain of thought (CoT) prompting? |
| 0.65 | supported | Chain-of-thought prompting is susceptible to adversarial attacks. | What is chain of thought (CoT) prompting? |
| 0.65 | supported | Evaluating qualitative improvements in reasoning from chain-of-thought prompting presents challenges. | What is chain of thought (CoT) prompting? |
| 0.65 | supported | Chain-of-thought prompting is not universally optimal; its effectiveness depends significantly on the model type and specific use case. | The Decreasing Value of Chain of Thought in Prompting |
| 0.65 | supported | For non-reasoning models, chain-of-thought prompting may improve average performance but can introduce inconsistency. | The Decreasing Value of Chain of Thought in Prompting |
| 0.45 | supported | For reasoning models, the minimal accuracy gains from chain-of-thought prompting rarely justify the increased response time. | The Decreasing Value of Chain of Thought in Prompting |
| 0.40 | insufficient | Zhang et al. (2025) showed that chain-of-thought prompting faces inherent limitations due to the complexity of navigating the prompt and answer spaces. | The Curse of CoT: On the Limitations of Chain-of-Thought in  |

**In what specific areas or tasks do researchers most strongly disagree about the benefits or drawbacks of chain-of-thought prompting?**

| Confidence | Status | Claim | Sources |
|---|---|---|---|
| 0.80 | supported | Researchers debate whether CoT prompting is universally beneficial across all task types, with some noting it is especially useful for complex tasks like math reasoning, symbolic manipulation, and commonsense questions, but not for simpler or non-decomposable tasks. | What is Chain of Thought Prompting? (2026); Chain-of-thought (CoT) prompting: Complete overview; Master Prompting Concepts: Chain of Thought Prompting |
| 0.80 | supported | There is consensus that CoT prompting enhances transparency and interpretability of model reasoning, but disagreement remains on whether these benefits justify its use in all domains. | What is Chain of Thought Prompting? (2026); Chain-of-thought (CoT) prompting: Complete overview; Master Prompting Concepts: Chain of Thought Prompting |
| 0.65 | supported | Some researchers highlight that CoT prompting introduces additional processing time and may require more refinement compared to zero-shot prompting, which is viewed as a drawback in certain applications. | What is Chain of Thought Prompting? (2026) |
| 0.62 | insufficient | Researchers strongly disagree about the effectiveness of chain-of-thought (CoT) prompting for tasks that do not require multi-step reasoning, with some evidence showing small or negative improvements for simple, single-step tasks. | Chain-of-thought (CoT) prompting: Complete overview; Master Prompting Concepts: Chain of Thought Prompting |
| 0.40 | insufficient | There is disagreement among researchers regarding the dependence of CoT prompting's effectiveness on model size, with some claiming it works best with very large language models (e.g., 100B+ parameters). | Chain-of-thought (CoT) prompting: Complete overview |
