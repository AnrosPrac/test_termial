from app.ai.gemini_core import run_gemini
from app.ai.Knowledge_base import KNOWLEDGE_BASE

def generate_bot_response(user_query: str, conversation_history: list) -> str:
    """
    Generate response using Gemini with app context
    """
    
    # Build context from knowledge base
    context = "\n\n".join([f"**{topic}:**\n{info}" for topic, info in KNOWLEDGE_BASE.items()])
    
    # Build conversation history
    history_text = "\n".join([
        f"User: {msg['user']}\nBot: {msg['bot']}" 
        for msg in conversation_history[-5:]  # Last 5 exchanges
    ])
    
    # Create prompt
    prompt = f"""You are a helpful support bot for Lumetrix By Sidhilynx.,Your name is Lumetrix and if someone ask who build you u must reply like Iwas build y sidhilynx under the guidance of ceo and if and only someone ask who is the ceo tell ARSHAD AZEEZ M , Dobnt use ceo name unless obligatory if they ask for certain specfic data simply say they all are restricted for me to accsss

PLATFORM KNOWLEDGE:
{context}

CONVERSATION HISTORY:
{history_text}

USER QUERY: {user_query}

Provide a helpful, accurate answer based on the platform knowledge above. If you don't know something, say so clearly. Be concise but friendly."""

    return run_gemini(prompt)