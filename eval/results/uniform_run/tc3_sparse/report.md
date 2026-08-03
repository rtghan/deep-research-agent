# Research Report: Comparison of Sparse Retrieval (BM25) vs. Dense Retrieval Methods

## Executive Summary
This report synthesizes findings on the comparative strengths and weaknesses of sparse retrieval methods, specifically BM25, and dense retrieval methods in various contexts. The analysis highlights fundamental differences, scenarios where each method excels, advantages of dense retrieval, performance metrics, and computational costs. The findings are based on verified claims from multiple sources.

## Findings

### What are the fundamental differences between sparse retrieval methods like BM25 and dense retrieval methods?
- Sparse retrieval methods like BM25 primarily rely on term-based ranking, effectively capturing relevance signals through statistical measures of term frequency and document length [Source: Integrating the Probabilistic Models BM25/BM25F into Lucene]. 
- Dense retrieval methods utilize contextualized language models, which can improve search effectiveness, particularly for complex queries [Source: On Single and Multiple Representations in Dense Passage Retrieval].
- BERT inference at query time for dense retrieval involves quadratic complexity, making it computationally intensive compared to BM25 [Source: On the Interpolation of Contextualized Term-based Ranking with BM25 for Query-by-Example Retrieval].
- Combining BM25 with contextualized models like TILDE has shown statistically significant improvements in retrieval performance [Source: On the Interpolation of Contextualized Term-based Ranking with BM25 for Query-by-Example Retrieval].

### In what scenarios or types of datasets does BM25 outperform dense retrieval methods?
- BM25 is particularly effective for retrieval on plain text documents and structured documents, where it can leverage its statistical foundation [Source: Integrating the Probabilistic Models BM25/BM25F into Lucene].
- Dense retrieval methods often struggle with noisy text, leading to substantial performance deterioration, while BM25 maintains robustness in such scenarios [Source: Analysing the Robustness of Dual Encoders for Dense Retrieval Against Misspellings].
- BM25 has been shown to perform well in environments where the text is clean and well-structured, as opposed to the typically curated datasets used for evaluating dense retrieval models [Source: Analysing the Robustness of Dual Encoders for Dense Retrieval Against Misspellings].

### What are the advantages of dense retrieval methods over sparse retrieval methods in information retrieval tasks?
- Dense retrieval methods have demonstrated gains in search effectiveness, particularly when applied for passage indexing and retrieval [Source: On Single and Multiple Representations in Dense Passage Retrieval].
- The use of multiple representations in dense retrieval yields better improvements for complex queries compared to single representations, which is a significant advantage over BM25 [Source: On Single and Multiple Representations in Dense Passage Retrieval].
- Dense retrieval is particularly beneficial for definitional queries and queries with complex information needs, where traditional sparse methods may fall short [Source: On Single and Multiple Representations in Dense Passage Retrieval].

### How do the performance metrics (e.g., precision, recall, F1 score) compare between sparse and dense retrieval methods in various applications?
- Performance metrics such as F1-score and Average Precision-Recall (AVPR) are sensitive to contamination rates in datasets, complicating direct comparisons between retrieval methods [Source: Anomaly Detection: How to Artificially Increase your F1-Score with a Biased Evaluation Protocol].
- In realistic retrieval settings, calculating recall is often infeasible due to the unknown total number of relevant documents, which affects the evaluation of both sparse and dense methods [Source: How important is Recall for Measuring Retrieval Quality?].
- Dense retrieval methods, while effective, can experience significant performance drops when faced with noisy inputs, indicating a need for careful dataset selection [Source: Analysing the Robustness of Dual Encoders for Dense Retrieval Against Misspellings].

### What are the computational costs and resource requirements for implementing sparse retrieval (BM25) versus dense retrieval methods?
- BM25 is computationally less intensive compared to dense retrieval methods, particularly those using BERT, which has quadratic complexity at query time [Source: On the Interpolation of Contextualized Term-based Ranking with BM25 for Query-by-Example Retrieval].
- Dense retrieval methods, especially those employing generative models, can introduce significant complexity and resource demands, particularly when using reinforcement learning techniques [Source: Lightweight and Direct Document Relevance Optimization for Generative Information Retrieval].
- The simplification of ranking optimization pipelines in generative information retrieval models can help mitigate some of the computational burdens associated with dense retrieval [Source: Lightweight and Direct Document Relevance Optimization for Generative Information Retrieval].

## Contradictions & Disagreements
- There is a discrepancy regarding the performance improvements attributed to DDRO (Document Relevance Optimization). One source claims a 9% improvement for Natural Questions [Source: Lightweight and Direct Document Relevance Optimization for Generative Information Retrieval], while another suggests a lower improvement of 2.9% [Source: Lightweight and Direct Document Relevance Optimization for Generative Information Retrieval]. This contradiction highlights the need for further investigation into the performance metrics of generative models.

## Known Gaps & Limitations
- There is insufficient evidence to definitively state the impact of latent terms in dense retrieval models on performance compared to BM25 and other models [Source: Latent Terms: Dense Retrievers Contain Trivially Extractable BM25-ready Zipfian Vocabularies].
- The complexities introduced by reinforcement learning-based methods in generative information retrieval are not well understood, indicating a gap in empirical data [Source: Lightweight and Direct Document Relevance Optimization for Generative Information Retrieval].
- The performance-efficiency trade-offs between fine-grained and coarse-grained retrieval models lack comprehensive empirical data, necessitating further research [Source: FiCo-ITR: bridging fine-grained and coarse-grained image-text retrieval for comparative performance analysis]. 

This report provides a structured overview of the comparative strengths and weaknesses of sparse and dense retrieval methods, highlighting areas for future research and practical considerations for implementation.