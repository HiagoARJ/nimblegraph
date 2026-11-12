module Api
  class PostsController < ApplicationController
    def index
      render json: Post.where(user_id: params[:user_id])
    end
  end
end
