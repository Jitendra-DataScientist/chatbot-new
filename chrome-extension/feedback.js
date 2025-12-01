/**
 * Feedback Manager - Modular feedback system for any product
 * Can be easily integrated into different applications
 */

class FeedbackManager {
  constructor(options = {}) {
    this.options = {
      productName: options.productName || 'Product',
      backendUrl: options.backendUrl || 'http://localhost:8502',
      apiEndpoint: options.apiEndpoint || '/api/feedback',
      onSubmitSuccess: options.onSubmitSuccess || (() => {}),
      onSubmitError: options.onSubmitError || (() => {}),
      customCss: options.customCss || '',
      logger: options.logger || console,
      ...options
    };
    
    this.isModalOpen = false;
    this.currentRating = 0;
    this.feedbackData = {
      rating: 0,
      complaints: '',
      improvements: '',
      timestamp: null,
      productName: this.options.productName
    };
    
    this.log('FeedbackManager initialized', { productName: this.options.productName });
  }
  
  log(message, data = null) {
    if (this.options.logger && this.options.logger.info) {
      this.options.logger.info(`[FeedbackManager] ${message}`, data);
    } else {
      console.log(`[FeedbackManager] ${message}`, data || '');
    }
  }
  
  /**
   * Create the feedback button that will be inserted into the header
   */
  createFeedbackButton() {
    const button = document.createElement('button');
    button.id = 'feedback-trigger-btn';
    button.className = 'feedback-trigger-button';
    button.innerHTML = '💬';
    button.title = 'Send Feedback';
    button.setAttribute('aria-label', 'Send feedback');
    
    // Let CSS handle all styling - no inline styles needed
    // All styling is now handled by .feedback-trigger-button CSS class
    
    button.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      this.openFeedbackModal();
    });
    
    this.log('Feedback button created');
    return button;
  }
  
  /**
   * Create the complete feedback modal
   */
  createFeedbackModal() {
    // Remove existing modal if any
    const existingModal = document.getElementById('feedback-modal');
    if (existingModal) {
      existingModal.remove();
    }
    
    const modal = document.createElement('div');
    modal.id = 'feedback-modal';
    modal.className = 'feedback-modal';
    
    modal.innerHTML = `
      <div class="feedback-modal-backdrop" id="feedback-backdrop"></div>
      <div class="feedback-modal-content">
        <div class="feedback-modal-header">
          <h3>Send Feedback - ${this.options.productName}</h3>
          <button class="feedback-close-btn" id="feedback-close-btn" aria-label="Close feedback">✕</button>
        </div>
        
        <div class="feedback-modal-body">
          <!-- Rating Section -->
          <div class="feedback-section">
            <label class="feedback-section-title">
              <span class="feedback-icon">⭐</span>
              Rate your experience
            </label>
            <div class="feedback-rating" id="feedback-rating">
              <button class="feedback-star" data-rating="1" aria-label="1 star">★</button>
              <button class="feedback-star" data-rating="2" aria-label="2 stars">★</button>
              <button class="feedback-star" data-rating="3" aria-label="3 stars">★</button>
              <button class="feedback-star" data-rating="4" aria-label="4 stars">★</button>
              <button class="feedback-star" data-rating="5" aria-label="5 stars">★</button>
            </div>
            <div class="feedback-rating-text" id="feedback-rating-text">Click to rate</div>
          </div>
          
          <!-- Complaints Section -->
          <div class="feedback-section">
            <label class="feedback-section-title" for="feedback-complaints">
              <span class="feedback-icon">⚠️</span>
              Issues or Complaints
            </label>
            <textarea 
              id="feedback-complaints" 
              class="feedback-textarea"
              placeholder="Describe any issues, bugs, or problems you encountered..."
              rows="3"
            ></textarea>
          </div>
          
          <!-- Improvements Section -->
          <div class="feedback-section">
            <label class="feedback-section-title" for="feedback-improvements">
              <span class="feedback-icon">💡</span>
              Suggestions for Improvement
            </label>
            <textarea 
              id="feedback-improvements" 
              class="feedback-textarea"
              placeholder="Share your ideas for making this better..."
              rows="3"
            ></textarea>
          </div>
        </div>
        
        <div class="feedback-modal-footer">
          <button class="feedback-btn feedback-btn-cancel" id="feedback-cancel-btn">Cancel</button>
          <button class="feedback-btn feedback-btn-submit" id="feedback-submit-btn">
            <span id="feedback-submit-text">Send Feedback</span>
            <span id="feedback-submit-loading" class="feedback-loading" style="display: none;">Sending...</span>
          </button>
        </div>
        
        <div class="feedback-success-message" id="feedback-success" style="display: none;">
          <div class="feedback-success-content">
            <span class="feedback-success-icon">✅</span>
            <span>Thank you for your feedback!</span>
          </div>
        </div>
      </div>
    `;
    
    document.body.appendChild(modal);
    this.setupModalEventListeners();
    this.log('Feedback modal created');
    
    return modal;
  }
  
  /**
   * Setup event listeners for the modal
   */
  setupModalEventListeners() {
    // Close button
    const closeBtn = document.getElementById('feedback-close-btn');
    if (closeBtn) {
      closeBtn.addEventListener('click', () => this.closeFeedbackModal());
    }
    
    // Cancel button
    const cancelBtn = document.getElementById('feedback-cancel-btn');
    if (cancelBtn) {
      cancelBtn.addEventListener('click', () => this.closeFeedbackModal());
    }
    
    // Backdrop click
    const backdrop = document.getElementById('feedback-backdrop');
    if (backdrop) {
      backdrop.addEventListener('click', () => this.closeFeedbackModal());
    }
    
    // ESC key
    document.addEventListener('keydown', this.handleKeyDown.bind(this));
    
    // Rating stars
    const stars = document.querySelectorAll('.feedback-star');
    stars.forEach(star => {
      star.addEventListener('click', (e) => {
        const rating = parseInt(e.target.dataset.rating);
        this.setRating(rating);
      });
      
      star.addEventListener('mouseenter', (e) => {
        const rating = parseInt(e.target.dataset.rating);
        this.highlightStars(rating);
      });
    });
    
    const ratingContainer = document.getElementById('feedback-rating');
    if (ratingContainer) {
      ratingContainer.addEventListener('mouseleave', () => {
        this.highlightStars(this.currentRating);
      });
    }
    
    // Submit button
    const submitBtn = document.getElementById('feedback-submit-btn');
    if (submitBtn) {
      submitBtn.addEventListener('click', () => this.submitFeedback());
    }
    
    // Input validation
    const textareas = document.querySelectorAll('.feedback-textarea');
    textareas.forEach(textarea => {
      textarea.addEventListener('input', () => this.validateForm());
    });
  }
  
  handleKeyDown(e) {
    if (e.key === 'Escape' && this.isModalOpen) {
      this.closeFeedbackModal();
    }
  }
  
  /**
   * Set rating and update UI
   */
  setRating(rating) {
    this.currentRating = rating;
    this.feedbackData.rating = rating;
    this.highlightStars(rating);
    this.updateRatingText(rating);
    this.validateForm();
    this.log('Rating set', { rating });
  }
  
  /**
   * Highlight stars up to the given rating
   */
  highlightStars(rating) {
    const stars = document.querySelectorAll('.feedback-star');
    stars.forEach((star, index) => {
      if (index < rating) {
        star.classList.add('feedback-star-active');
      } else {
        star.classList.remove('feedback-star-active');
      }
    });
  }
  
  /**
   * Update rating text
   */
  updateRatingText(rating) {
    const ratingText = document.getElementById('feedback-rating-text');
    if (ratingText) {
      const texts = {
        1: '⭐ Poor',
        2: '⭐⭐ Fair', 
        3: '⭐⭐⭐ Good',
        4: '⭐⭐⭐⭐ Very Good',
        5: '⭐⭐⭐⭐⭐ Excellent'
      };
      ratingText.textContent = texts[rating] || 'Click to rate';
    }
  }
  
  /**
   * Validate form and enable/disable submit button
   */
  validateForm() {
    const submitBtn = document.getElementById('feedback-submit-btn');
    const hasRating = this.currentRating > 0;
    const complaintsText = document.getElementById('feedback-complaints')?.value.trim() || '';
    const improvementsText = document.getElementById('feedback-improvements')?.value.trim() || '';
    const hasContent = complaintsText.length > 0 || improvementsText.length > 0;
    
    const isValid = hasRating || hasContent; // Either rating or some text content
    
    if (submitBtn) {
      submitBtn.disabled = !isValid;
      if (isValid) {
        submitBtn.classList.add('feedback-btn-enabled');
      } else {
        submitBtn.classList.remove('feedback-btn-enabled');
      }
    }
  }
  
  /**
   * Open the feedback modal
   */
  openFeedbackModal() {
    if (this.isModalOpen) return;
    
    const modal = this.createFeedbackModal();
    this.isModalOpen = true;
    
    // Initialize button states after modal is created
    setTimeout(() => {
      this.resetButtonStates();
    }, 50);
    
    // Animate in
    setTimeout(() => {
      modal.classList.add('feedback-modal-visible');
    }, 10);
    
    // Focus first interactive element
    const firstStar = modal.querySelector('.feedback-star');
    if (firstStar) {
      firstStar.focus();
    }
    
    this.log('Feedback modal opened');
  }
  
  /**
   * Close the feedback modal
   */
  closeFeedbackModal() {
    const modal = document.getElementById('feedback-modal');
    if (!modal || !this.isModalOpen) return;
    
    modal.classList.remove('feedback-modal-visible');
    
    setTimeout(() => {
      modal.remove();
      this.isModalOpen = false;
      document.removeEventListener('keydown', this.handleKeyDown.bind(this));
    }, 300);
    
    // Reset form data
    this.resetForm();
    this.log('Feedback modal closed');
  }
  
  /**
   * Reset form data and UI states
   */
  resetForm() {
    this.currentRating = 0;
    this.feedbackData = {
      rating: 0,
      complaints: '',
      improvements: '',
      timestamp: null,
      productName: this.options.productName
    };
    
    // Reset button states to initial state
    this.resetButtonStates();
  }
  
  /**
   * Reset submit button to initial state
   */
  resetButtonStates() {
    const submitBtn = document.getElementById('feedback-submit-btn');
    const submitText = document.getElementById('feedback-submit-text');
    const submitLoading = document.getElementById('feedback-submit-loading');
    
    if (submitBtn) {
      submitBtn.disabled = false;
    }
    if (submitText) {
      submitText.style.display = 'inline';
    }
    if (submitLoading) {
      submitLoading.style.display = 'none';
    }
    
    this.log('Button states reset to initial state');
  }
  
  /**
   * Submit feedback to backend
   */
  async submitFeedback() {
    const submitBtn = document.getElementById('feedback-submit-btn');
    const submitText = document.getElementById('feedback-submit-text');
    const submitLoading = document.getElementById('feedback-submit-loading');
    const complaintsInput = document.getElementById('feedback-complaints');
    const improvementsInput = document.getElementById('feedback-improvements');
    
    // Prepare data
    this.feedbackData.rating = this.currentRating;
    this.feedbackData.complaints = complaintsInput?.value.trim() || '';
    this.feedbackData.improvements = improvementsInput?.value.trim() || '';
    this.feedbackData.timestamp = new Date().toISOString();
    this.feedbackData.userAgent = navigator.userAgent;
    this.feedbackData.url = window.location.href;
    
    // Show loading state
    if (submitBtn) submitBtn.disabled = true;
    if (submitText) submitText.style.display = 'none';
    if (submitLoading) submitLoading.style.display = 'inline-flex';
    
    this.log('Submitting feedback', {
      url: `${this.options.backendUrl}${this.options.apiEndpoint}`,
      data: this.feedbackData,
      usingProxy: typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage
    });
    
    try {
      // Pre-flight check: verify backend URL is accessible
      this.log('Testing backend connection...');
      
      // Send to backend
      const response = await this.sendFeedbackToBackend(this.feedbackData);
      
      this.log('Feedback submission response', response);
      
      if (response && response.success) {
        this.showSuccessMessage();
        this.options.onSubmitSuccess(this.feedbackData, response);
        
        // Auto-close after success
        setTimeout(() => {
          this.closeFeedbackModal();
        }, 2000);
        
      } else {
        throw new Error(response?.error || 'Backend returned unsuccessful response');
      }
      
    } catch (error) {
      this.log('Feedback submission failed', { 
        error: error.message,
        errorType: error.name,
        stack: error.stack,
        backendUrl: this.options.backendUrl,
        endpoint: this.options.apiEndpoint
      });
      
      this.options.onSubmitError(error, this.feedbackData);
      
      // Reset button state
      this.resetButtonStates();
      
      // Show detailed error message
      let errorMessage = error.message;
      if (error.message.includes('Failed to fetch')) {
        errorMessage = `Cannot connect to backend server (${this.options.backendUrl}). Please ensure your Flask app is running on port 8502.`;
      } else if (error.message.includes('No response from background')) {
        errorMessage = 'Extension communication error. Please reload the extension and try again.';
      }
      
      this.showErrorMessage(errorMessage);
    }
  }
  
  /**
   * Send feedback to backend (uses Chrome extension proxy if available)
   */
  async sendFeedbackToBackend(feedbackData) {
    const url = `${this.options.backendUrl}${this.options.apiEndpoint}`;
    
    // Check if we're in a Chrome extension context and use proxy
    if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.sendMessage) {
      this.log('Using Chrome extension proxy for feedback submission');
      
      return new Promise((resolve, reject) => {
        chrome.runtime.sendMessage(
          {
            action: 'proxyFetch',
            url: url,
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(feedbackData),
            timeoutMs: 30000
          },
          (response) => {
            const lastError = chrome.runtime.lastError;
            if (lastError) {
              return reject(new Error(lastError.message));
            }
            if (!response) {
              return reject(new Error('No response from background script'));
            }
            if (response.success === false) {
              return reject(new Error(response.error || 'Proxy fetch failed'));
            }
            
            try {
              const result = JSON.parse(response.body || '{}');
              resolve(result);
            } catch (e) {
              reject(new Error('Failed to parse response: ' + e.message));
            }
          }
        );
      });
    } else {
      // Fallback to direct fetch for non-extension environments
      this.log('Using direct fetch for feedback submission');
      
      const response = await fetch(url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(feedbackData)
      });
      
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }
      
      const result = await response.json();
      return result;
    }
  }
  
  /**
   * Show success message
   */
  showSuccessMessage() {
    const successDiv = document.getElementById('feedback-success');
    const modalBody = document.querySelector('.feedback-modal-body');
    const modalFooter = document.querySelector('.feedback-modal-footer');
    
    if (successDiv && modalBody && modalFooter) {
      modalBody.style.display = 'none';
      modalFooter.style.display = 'none';
      successDiv.style.display = 'flex';
    }
  }
  
  /**
   * Show error message
   */
  showErrorMessage(message) {
    // Create temporary error message
    const errorDiv = document.createElement('div');
    errorDiv.className = 'feedback-error-message';
    errorDiv.innerHTML = `
      <div class="feedback-error-content">
        <span class="feedback-error-icon">❌</span>
        <span>Failed to send feedback: ${message}</span>
      </div>
    `;
    
    const modalContent = document.querySelector('.feedback-modal-content');
    if (modalContent) {
      modalContent.appendChild(errorDiv);
      
      // Remove after 5 seconds
      setTimeout(() => {
        errorDiv.remove();
      }, 5000);
    }
  }
  
  /**
   * Static method to create and integrate feedback button into an existing header
   */
  static integrate(headerElement, options = {}) {
    const feedbackManager = new FeedbackManager(options);
    const feedbackButton = feedbackManager.createFeedbackButton();
    
    // Insert before the last child (usually minimize button)
    if (headerElement.lastElementChild) {
      headerElement.insertBefore(feedbackButton, headerElement.lastElementChild);
    } else {
      headerElement.appendChild(feedbackButton);
    }
    
    return feedbackManager;
  }
}

// Export for use in other scripts - ensure it's always available
try {
  if (typeof window !== 'undefined') {
    window.FeedbackManager = FeedbackManager;
  }
  
  // Also support module exports if needed
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = FeedbackManager;
  }
  
  // Global export for Chrome extensions
  if (typeof globalThis !== 'undefined') {
    globalThis.FeedbackManager = FeedbackManager;
  }
  
  console.log('[FeedbackManager] Successfully exported FeedbackManager class');
} catch (error) {
  console.error('[FeedbackManager] Failed to export FeedbackManager:', error);
}
