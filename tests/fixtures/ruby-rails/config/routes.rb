Rails.application.routes.draw do
  root "home#index"
  namespace :api do
    resources :users, only: [:index, :show, :create] do
      resources :posts, only: [:index]
      member do
        post :activate
      end
    end
  end
  get "/health", to: "health#show"
end
