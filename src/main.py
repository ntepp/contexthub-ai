import sys
from src.rag.rag_pipeline import create_and_run_graph, load_source

def main():
    """Main entry point for the interactive RAG application."""
    print("Welcome to the Interactive RAG Application!")

    source = None  # Initially no source set

    while True:
        if not source:
            source = input("\nEnter URL or local path (or type 'exit' to quit): ").strip()
            if source.lower() == 'exit':
                print("Goodbye!")
                break
            if not source:
                print("Invalid input. Please provide a valid source.")
                continue

            print("Loading source...")
            try:
                load_source(source)
                print("Source loaded successfully!")
            except Exception as e:
                print(f"Error loading source: {e}")
                source = None  # Reset source on error.
                continue

        while True:
            question = input("\nEnter your question (or type 'set source' to add a source, 'exit' to quit): ").strip()

            if question.lower() == 'exit':
                print("Goodbye!")
                return
            if question.lower() == 'set source':
                source = None
                print("You can now set a new source.")
                break  # Go back to setting a new source

            try:
                result = create_and_run_graph(question)
                print("\nAnswer:", result["answer"])
                if result["sources"]:
                    unique = {s["source"] for s in result["sources"]}
                    print("Sources:", ", ".join(unique))
            except Exception as e:
                print(f"\nAn error occurred: {e}")

            print("-" * 40)

if __name__ == "__main__":
    main()