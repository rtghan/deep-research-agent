# Research Report: Comparison of Sparse Retrieval (BM25) vs. Dense Retrieval Methods

## Executive Summary
This report synthesizes current research on the effectiveness and use cases of sparse retrieval methods, particularly BM25, compared to dense retrieval methods such as BERT and other transformer-based models. The findings indicate that while both methods have their strengths and weaknesses, they excel in different contexts. Sparse retrieval methods are particularly effective for precise keyword matching, while dense retrieval methods shine in semantic search tasks. Hybrid approaches that combine both methods are increasingly recognized for their superior performance in diverse retrieval scenarios.

## Findings

### What are the fundamental principles and mechanisms of sparse retrieval methods, specifically BM25?
1. BM25 is the most widely used ranking function in sparse retrieval, although other methods are also utilized in specific contexts [Source: How to Implement Sparse Retrieval; Hybrid Retrieval: Combining Sparse and Dense Methods ...; Dense vs. Sparse Retrieval: What They Are, Differences, and Best Strategies].
2. BM25 improves upon TF-IDF by adding document length normalization and term frequency saturation [Source: How to Implement Sparse Retrieval].
3. Sparse retrieval assigns weights to individual terms and retrieves documents based on shared terms between the query and the document [Source: Dense vs. Sparse Retrieval: What They Are, Differences, and Best Strategies].
4. BM25 is a probabilistic ranking function that enhances TF-IDF [Source: Dense vs. Sparse Retrieval: What They Are, Differences, and Best Strategies].
5. Sparse retrieval methods like BM25 operate on exact term matching and inverse document frequency statistics [Source: Hybrid Retrieval: Combining Sparse and Dense Methods ...].
6. BM25 adjusts term importance using parameters k1 and b, where k1 controls term frequency saturation and b adjusts for document length normalization [Source: Dense vs. Sparse Retrieval: What They Are, Differences, and Best Strategies; How to Implement Sparse Retrieval].
7. Sparse retrieval methods excel in scenarios requiring precise keyword matching, though there are scenarios where dense retrieval methods may outperform them [Source: Hybrid Retrieval: Combining Sparse and Dense Methods ...; Dense vs. Sparse Retrieval: What They Are, Differences, and Best Strategies].
8. Production search systems rely heavily on sparse retrieval methods like BM25 in scenarios requiring precise keyword matching, though other retrieval methods are also employed in different contexts [Source: Hybrid Retrieval: Combining Sparse and Dense Methods ...; How to Implement Sparse Retrieval].

### What are the fundamental principles and mechanisms of dense retrieval methods, including examples like BERT or other transformer-based models?
1. Dense Retrieval Models (DRMs) embed queries and documents in a shared vector space to enhance semantic search [Source: Dense Retrieval Models: Principles & Advances].
2. Pre-trained language models like BERT and T5 serve as crucial backbone encoders for dense retrieval, though other architectures such as RoBERTa, MiniLM, and decoder-only models are also employed [Source: Large Language Models as Foundations for Next-Gen Dense ...; Dense Retrieval Models: Principles & Advances].
3. The BERT re-ranker uses the concatenation of a query and candidate document as the input to a fine-tuned pre-trained BERT model [Source: Improving BERT-based Query-by-Document Retrieval with Multi-Task Optimization].
4. Dense retrieval methods utilize vector embeddings for semantic search [Source: Dense vs. Sparse Retrieval: What They Are, Differences, ...].
5. Dense retrieval excels at paraphrase and conceptual queries relative to sparse methods like BM25, though it may underperform on exact-match queries [Source: Hybrid Search: BM25, Vector & Reranking Reference 2026; Hybrid Retrieval: Combining Sparse and Dense Methods ...].
6. Transformer-based ranking models have proven to be highly effective at taking advantage of context in specific retrieval tasks, though they face challenges related to input length and versatility [Source: Improving BERT-based Query-by-Document Retrieval with Multi-Task Optimization].

### In what scenarios or types of data do sparse retrieval methods like BM25 outperform dense retrieval methods?
1. Sparse retrieval methods like BM25 outperform dense retrieval methods in scenarios requiring precise keyword matching, particularly in financial documents [Source: From BM25 to Corrective RAG: Benchmarking Retrieval ...; Hybrid Retrieval: Combining Sparse and Dense Methods ...].
2. Sparse retrieval methods excel at retrieving documents containing specific entity names in exact matching scenarios with precise terminology, particularly in financial documents [Source: From BM25 to Corrective RAG: Benchmarking Retrieval ...; Hybrid Retrieval: Combining Sparse and Dense Methods ...].
3. Dense retrieval methods struggle with exact matching scenarios involving precise terminology like entity names and domain-specific terms, where sparse methods excel [Source: From BM25 to Corrective RAG: Benchmarking Retrieval ...; Hybrid Retrieval: Combining Sparse and Dense Methods ...].
4. BM25 provides fast retrieval with interpretable scoring that can be understood and debugged by practitioners, particularly in scenarios requiring precise keyword matching [Source: Hybrid Retrieval: Combining Sparse and Dense Methods ...].

### In what scenarios or types of data do dense retrieval methods outperform sparse retrieval methods like BM25?
1. Dense retrieval methods may underperform sparse retrieval methods in scenarios with exact matches for rare terms or entities, though they may perform comparably on other types of exact matches [Source: Hybrid Search: BM25, Vector & Reranking Reference 2026; Hybrid Retrieval: Combining Sparse and Dense Methods ...].
2. Dense retrieval excels at paraphrase and conceptual queries relative to sparse methods like BM25, though it may underperform on exact-match queries [Source: Hybrid Search: BM25, Vector & Reranking Reference 2026; Hybrid Retrieval: Combining Sparse and Dense Methods ...].
3. Sparse retrieval methods like BM25 outperform dense retrieval methods on exact-match queries, particularly for product codes and named entities, while hybrid approaches dominate in mixed scenarios [Source: Hybrid Search: BM25, Vector & Reranking Reference 2026; Hybrid Retrieval: Combining Sparse and Dense Methods ...].

### What are the current trends and advancements in both sparse and dense retrieval methods as of 2023?
1. Hybrid retrieval approaches provide frameworks for integrating innovations with existing methods [Source: Hybrid Retrieval: Combining Sparse and Dense Methods ...].
2. Interpolation of sparse and dense retrieval results can enhance retrieval effectiveness for highly relevant passages, though its effectiveness may vary for passages of lower relevance [Source: To Interpolate or not to Interpolate: PRF, Dense and Sparse Retrievers].
3. Neural Information Retrieval (IR) models have produced promising results in the ad-hoc domain, though their deployment in sensitive tasks may be limited by challenges related to interpretability and control [Source: Overcoming low-utility facets for complex answer retrieval; Interpret and Control Dense Retrieval with Sparse Latent Features].
4. The combination of dense and sparse retrieval methods improves overall performance on various retrieval tasks, though specific contexts and metrics may yield different results [Source: Dense vs. Sparse Retrieval: What They Are, Differences, ...].

## Contradictions & Disagreements
1. There is disagreement about whether hybrid retrieval approaches consistently outperform single-method retrieval across all tasks. While some evidence suggests that hybrid methods show advantages in broader, diverse tasks, they do not consistently outperform single methods, particularly in exact-match scenarios where sparse methods excel [Source: Hybrid Search: BM25, Vector & Reranking Reference 2026; Hybrid Retrieval: Combining Sparse and Dense Methods ...].
2. Dense retrieval methods may struggle with exact matching scenarios involving rare-term matches, such as product codes and entity names, where sparse methods excel. However, there are contexts where dense retrieval can perform comparably or even better on certain exact matches [Source: Hybrid Search: BM25, Vector & Reranking Reference 2026; Hybrid Retrieval: Combining Sparse and Dense Methods ...].

## How Claims Changed
1. Claims regarding the performance of BM25 were refined to acknowledge that while it excels in precise keyword matching, dense retrieval methods may outperform it in specific contexts, such as definitional queries and complex information needs.
2. Claims about the effectiveness of dense retrieval methods were reversed to reflect that they may underperform in exact-match scenarios, particularly for rare terms or entities, while sparse methods excel in these contexts.
3. Several claims were narrowed to specify the contexts in which certain retrieval methods perform best, acknowledging the limitations and strengths of each method based on the evidence.

## Known Gaps & Limitations
1. There is insufficient evidence regarding the performance of dense retrieval methods in zero-shot settings across various datasets, which limits the understanding of their generalizability.
2. The effectiveness of hybrid retrieval methods in specific domain contexts remains underexplored, particularly in relation to their performance compared to pure sparse or dense methods.
3. The impact of emerging techniques, such as learned sparse models and advanced interpolation methods, on retrieval effectiveness requires further investigation to establish their practical implications in real-world applications.