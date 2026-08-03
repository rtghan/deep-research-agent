# Research Report: Comparison of Sparse Retrieval (BM25) vs. Dense Retrieval Methods

## Executive Summary
This report synthesizes findings on the differences between sparse retrieval methods, specifically BM25, and dense retrieval methods. It explores their underlying algorithms, performance in various scenarios, computational efficiency, and recent advancements. The findings indicate that while dense retrieval methods are becoming the standard due to their efficiency and effectiveness, sparse methods like BM25 still hold advantages in specific contexts, particularly when faced with noisy data.

## Findings

### What are the fundamental differences between sparse retrieval methods (e.g., BM25) and dense retrieval methods in terms of their underlying algorithms?
- Term-based ranking with pre-trained transformer-based language models enhances the contextualization power of term-based retrieval methods like BM25 [Source: On the Interpolation of Contextualized Term-based Ranking with BM25 for Query-by-Example Retrieval].
- BERT inference at query time exhibits quadratic complexity, contrasting with the linear complexity typically associated with BM25 [Source: On the Interpolation of Contextualized Term-based Ranking with BM25 for Query-by-Example Retrieval].
- Dense retrieval can utilize single or multiple representations of passages, with multiple representations providing better performance for complex queries [Source: On Single and Multiple Representations in Dense Passage Retrieval].
- Combining BM25 with models like TILDE can yield statistically significant improvements in retrieval tasks [Source: On the Interpolation of Contextualized Term-based Ranking with BM25 for Query-by-Example Retrieval].

### In what scenarios or types of datasets is BM25 (sparse retrieval) shown to outperform dense retrieval methods?
- Dense retrieval models can significantly deteriorate in performance when dealing with noisy user-generated text, suggesting that BM25 may perform better in such scenarios [Source: Analysing the Robustness of Dual Encoders for Dense Retrieval Against Misspellings].
- There is insufficient evidence to conclusively determine specific scenarios where BM25 consistently outperforms dense retrieval methods, as claims regarding its superiority in definitional queries and complex information needs are not strongly supported.

### What are the advantages of dense retrieval methods over sparse retrieval methods in terms of performance and accuracy?
- Dense retrieval models are increasingly recognized as the standard for document and passage ranking due to their efficiency and high performance [Source: Analysing the Robustness of Dual Encoders for Dense Retrieval Against Misspellings].
- The dual-encoder architecture is particularly effective for scoring question-passage pairs, and multiple representations in dense retrieval have shown to improve outcomes for difficult queries [Source: On Single and Multiple Representations in Dense Passage Retrieval].
- Dense retrieval methods have demonstrated improved effectiveness when used for re-ranking outputs from classical sparse models like BM25 [Source: On Single and Multiple Representations in Dense Passage Retrieval].

### How do the computational requirements and efficiency of sparse retrieval (BM25) compare to those of dense retrieval methods?
- BM25 is generally more efficient due to its linear complexity compared to the quadratic complexity of BERT inference in dense retrieval [Source: On the Interpolation of Contextualized Term-based Ranking with BM25 for Query-by-Example Retrieval].
- Dense retrieval methods have improved search effectiveness when directly applied for passage indexing and retrieval, indicating a shift towards their adoption in practical applications [Source: On Single and Multiple Representations in Dense Passage Retrieval].

### What recent advancements or techniques have been introduced in dense retrieval methods that enhance their effectiveness compared to traditional sparse methods like BM25?
- Recent advancements in dense retrieval techniques allow for the handling of out-of-vocabulary (OOV) words, which are critical in speech recognition contexts [Source: A Method for Open-Vocabulary Speech-Driven Text Retrieval].
- The introduction of single-visit methods offers an alternative to traditional approaches, potentially enhancing the efficiency of resource selection in retrieval tasks [Source: Revisiting resource selection probability functions and single-visit methods: Clarification and extensions].

## Contradictions & Disagreements
- There is disagreement regarding the scenarios in which BM25 outperforms dense retrieval methods. Some sources claim that BM25 excels in handling complex queries and definitional queries, while others suggest that dense retrieval methods may be more robust overall, particularly in the presence of noise [Source: On Single and Multiple Representations in Dense Passage Retrieval; Analysing the Robustness of Dual Encoders for Dense Retrieval Against Misspellings].

## Known Gaps & Limitations
- There is insufficient evidence to clearly define the specific types of queries or datasets where BM25 consistently outperforms dense retrieval methods. Further research is needed to explore these scenarios comprehensively.
- The performance metrics of the best systems in two-stage retrieval setups remain unclear, as confidence in their reported effectiveness is low [Source: DS@GT at TREC TOT 2025: Bridging Vague Recollection with Fusion Retrieval and Learned Reranking]. 

This report highlights the evolving landscape of information retrieval methods, emphasizing the need for ongoing research to fully understand the strengths and weaknesses of both sparse and dense retrieval techniques.