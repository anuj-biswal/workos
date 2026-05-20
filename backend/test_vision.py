import os
from agent import graph
from langchain_core.messages import HumanMessage

def test():
    config = {"configurable": {"thread_id": "test-vision-123"}}
    
    # We will assume a chart called 'sample.png' exists in the workspace
    messages = [
        HumanMessage(content="can you look at the chart sample.png and summarize what is going on?")
    ]
    
    initial_state = {"messages": messages, "workspace_id": "default-workspace"}
    
    # Create a dummy image for testing
    workspace_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspaces", "default-workspace")
    os.makedirs(workspace_path, exist_ok=True)
    img_path = os.path.join(workspace_path, "sample.png")
    
    # Write a tiny 1x1 black png
    with open(img_path, "wb") as f:
        f.write(b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\x0d\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
    
    print("Testing prompt: 'can you look at the chart sample.png and summarize what is going on?'\n")
    
    for _ in graph.stream(initial_state, config):
        pass
        
    current_state = graph.get_state(config)
    plan = current_state.values.get("plan", [])
    
    if plan:
        print(f"Agent generated a plan with {len(plan)} tools.")
        for step in plan:
            print(f" - Tool: {step['tool']}, Args: {step['args']}")
            if step['tool'] == 'analyze_image':
                print("SUCCESS: Agent correctly chose to use analyze_image!")
    else:
        print("FAILED: No tools planned. The agent decided to reply directly.")
        

if __name__ == "__main__":
    test()
