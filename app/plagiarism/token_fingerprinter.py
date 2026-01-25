"""
Token Fingerprinting using Winnowing Algorithm
Robust to variable renaming and minor code changes

Based on: "Winnowing: Local Algorithms for Document Fingerprinting" (Schleimer et al., 2003)
"""

import hashlib
from typing import List, Set, Tuple, Dict
from collections import deque


class TokenFingerprinter:
    """
    Winnowing-based code fingerprinting
    Detects similarity even with renamed variables
    """
    
    # Winnowing parameters
    K_GRAM_SIZE = 5     # Size of each k-gram
    WINDOW_SIZE = 4     # Window size for selecting fingerprints
    
    def __init__(self):
        self.tokenizer = CodeTokenizer()
    
    async def compare(
        self,
        code1: str,
        code2: str,
        language: str
    ) -> Tuple[float, Dict]:
        """
        Compare two code submissions using token fingerprinting
        
        Returns:
            (similarity_score, details_dict)
        """
        try:
            # Tokenize code
            tokens1 = self.tokenizer.tokenize(code1, language)
            tokens2 = self.tokenizer.tokenize(code2, language)
            
            # Generate fingerprints using Winnowing
            fingerprints1 = self._winnow(tokens1)
            fingerprints2 = self._winnow(tokens2)
            
            # Calculate Jaccard similarity
            similarity = self._jaccard_similarity(fingerprints1, fingerprints2)
            
            # Generate details
            details = {
                "token_count1": len(tokens1),
                "token_count2": len(tokens2),
                "fingerprint_count1": len(fingerprints1),
                "fingerprint_count2": len(fingerprints2),
                "common_fingerprints": len(fingerprints1 & fingerprints2),
                "token_overlap": self._calculate_token_overlap(tokens1, tokens2)
            }
            
            return similarity, details
            
        except Exception as e:
            return 0.0, {"error": str(e)}
    
    def _winnow(self, tokens: List[str]) -> Set[int]:
        """
        Apply Winnowing algorithm to generate document fingerprints
        
        Args:
            tokens: List of code tokens
        
        Returns:
            Set of fingerprint hashes
        """
        if len(tokens) < self.K_GRAM_SIZE:
            return set()
        
        # Step 1: Generate k-grams
        k_grams = []
        for i in range(len(tokens) - self.K_GRAM_SIZE + 1):
            k_gram = tuple(tokens[i:i + self.K_GRAM_SIZE])
            k_grams.append(k_gram)
        
        # Step 2: Hash each k-gram
        hashes = []
        for i, k_gram in enumerate(k_grams):
            hash_value = self._hash_kgram(k_gram)
            hashes.append((hash_value, i))
        
        # Step 3: Apply Winnowing (select minimum hash in each window)
        fingerprints = set()
        window = deque()
        min_pos = 0
        
        for i, (hash_val, pos) in enumerate(hashes):
            # Remove elements outside current window
            while window and window[0][1] <= i - self.WINDOW_SIZE:
                window.popleft()
            
            # Add current hash
            while window and window[-1][0] > hash_val:
                window.pop()
            window.append((hash_val, i))
            
            # Select fingerprint (rightmost minimum in window)
            if i >= self.WINDOW_SIZE - 1:
                # Add minimum hash in window
                min_hash = window[0][0]
                fingerprints.add(min_hash)
        
        return fingerprints
    
    def _hash_kgram(self, k_gram: Tuple[str, ...]) -> int:
        """Hash a k-gram into an integer"""
        text = ''.join(k_gram)
        return int(hashlib.md5(text.encode()).hexdigest()[:8], 16)
    
    def _jaccard_similarity(self, set1: Set, set2: Set) -> float:
        """Calculate Jaccard similarity between two sets"""
        if not set1 and not set2:
            return 1.0
        
        if not set1 or not set2:
            return 0.0
        
        intersection = len(set1 & set2)
        union = len(set1 | set2)
        
        return intersection / union if union > 0 else 0.0
    
    def _calculate_token_overlap(
        self,
        tokens1: List[str],
        tokens2: List[str]
    ) -> float:
        """Calculate percentage of overlapping tokens"""
        set1 = set(tokens1)
        set2 = set(tokens2)
        
        if not set1 or not set2:
            return 0.0
        
        overlap = len(set1 & set2)
        total = len(set1 | set2)
        
        return overlap / total if total > 0 else 0.0


class CodeTokenizer:
    """Tokenize code into meaningful tokens"""
    
    def tokenize(self, code: str, language: str) -> List[str]:
        """
        Tokenize code into list of tokens
        
        Args:
            code: Source code
            language: Programming language
        
        Returns:
            List of tokens
        """
        if language == 'python':
            return self._tokenize_python(code)
        elif language in ['c', 'cpp']:
            return self._tokenize_c(code)
        else:
            return self._tokenize_generic(code)
    
    def _tokenize_python(self, code: str) -> List[str]:
        """Tokenize Python code"""
        import tokenize
        import io
        
        tokens = []
        try:
            readline = io.BytesIO(code.encode()).readline
            token_gen = tokenize.tokenize(readline)
            
            for tok in token_gen:
                # Skip comments, newlines, encoding
                if tok.type in [
                    tokenize.COMMENT,
                    tokenize.NL,
                    tokenize.NEWLINE,
                    tokenize.ENCODING,
                    tokenize.ENDMARKER
                ]:
                    continue
                
                # Normalize names (variables) to 'VAR'
                if tok.type == tokenize.NAME and not self._is_keyword(tok.string):
                    tokens.append('VAR')
                # Normalize numbers to 'NUM'
                elif tok.type == tokenize.NUMBER:
                    tokens.append('NUM')
                # Normalize strings to 'STR'
                elif tok.type == tokenize.STRING:
                    tokens.append('STR')
                # Keep operators and keywords as-is
                else:
                    tokens.append(tok.string)
        
        except tokenize.TokenError:
            # Fallback to simple tokenization
            return self._tokenize_generic(code)
        
        return tokens
    
    def _tokenize_c(self, code: str) -> List[str]:
        """Tokenize C/C++ code"""
        import re
        
        # Remove comments
        code = re.sub(r'//.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
        
        # Token pattern
        pattern = r'''
            \b\d+\b |                    # Numbers
            \b[a-zA-Z_]\w*\b |           # Identifiers/keywords
            [+\-*/%=<>!&|^~] |           # Operators
            [(){}\[\];,.]                # Punctuation
        '''
        
        tokens = []
        for match in re.finditer(pattern, code, re.VERBOSE):
            token = match.group(0)
            
            # Normalize identifiers (not keywords)
            if re.match(r'[a-zA-Z_]\w*', token):
                if not self._is_c_keyword(token):
                    tokens.append('VAR')
                else:
                    tokens.append(token)
            # Normalize numbers
            elif re.match(r'\d+', token):
                tokens.append('NUM')
            # Keep operators and punctuation
            else:
                tokens.append(token)
        
        return tokens
    
    def _tokenize_generic(self, code: str) -> List[str]:
        """Generic tokenization (fallback)"""
        import re
        
        # Remove comments (try both styles)
        code = re.sub(r'#.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'//.*$', '', code, flags=re.MULTILINE)
        code = re.sub(r'/\*.*?\*/', '', code, flags=re.DOTALL)
        
        # Split on whitespace and special chars
        pattern = r'\w+|[^\w\s]'
        tokens = re.findall(pattern, code)
        
        return [t for t in tokens if t.strip()]
    
    def _is_keyword(self, word: str) -> bool:
        """Check if word is a Python keyword"""
        import keyword
        return keyword.iskeyword(word)
    
    def _is_c_keyword(self, word: str) -> bool:
        """Check if word is a C/C++ keyword"""
        c_keywords = {
            'auto', 'break', 'case', 'char', 'const', 'continue',
            'default', 'do', 'double', 'else', 'enum', 'extern',
            'float', 'for', 'goto', 'if', 'int', 'long', 'register',
            'return', 'short', 'signed', 'sizeof', 'static', 'struct',
            'switch', 'typedef', 'union', 'unsigned', 'void', 'volatile',
            'while', 'class', 'namespace', 'template', 'typename',
            'public', 'private', 'protected', 'virtual', 'override'
        }
        return word in c_keywords


class MinHashFingerprinter:
    """
    Alternative: MinHash-based fingerprinting
    Faster for large-scale comparisons
    """
    
    def __init__(self, num_hashes: int = 128):
        """
        Initialize MinHash fingerprinter
        
        Args:
            num_hashes: Number of hash functions to use
        """
        self.num_hashes = num_hashes
        self.tokenizer = CodeTokenizer()
    
    async def compare(
        self,
        code1: str,
        code2: str,
        language: str
    ) -> Tuple[float, Dict]:
        """Compare using MinHash"""
        try:
            # Tokenize
            tokens1 = set(self.tokenizer.tokenize(code1, language))
            tokens2 = set(self.tokenizer.tokenize(code2, language))
            
            # Generate MinHash signatures
            sig1 = self._minhash_signature(tokens1)
            sig2 = self._minhash_signature(tokens2)
            
            # Estimate Jaccard similarity
            similarity = self._estimate_similarity(sig1, sig2)
            
            details = {
                "unique_tokens1": len(tokens1),
                "unique_tokens2": len(tokens2),
                "signature_size": len(sig1),
                "matching_hashes": sum(1 for a, b in zip(sig1, sig2) if a == b)
            }
            
            return similarity, details
            
        except Exception as e:
            return 0.0, {"error": str(e)}
    
    def _minhash_signature(self, tokens: Set[str]) -> List[int]:
        """Generate MinHash signature for token set"""
        signature = []
        
        for i in range(self.num_hashes):
            min_hash = float('inf')
            
            for token in tokens:
                # Hash token with seed i
                hash_val = hash((token, i)) & 0x7FFFFFFF
                min_hash = min(min_hash, hash_val)
            
            signature.append(min_hash)
        
        return signature
    
    def _estimate_similarity(self, sig1: List[int], sig2: List[int]) -> float:
        """Estimate Jaccard similarity from MinHash signatures"""
        matches = sum(1 for a, b in zip(sig1, sig2) if a == b)
        return matches / len(sig1)
