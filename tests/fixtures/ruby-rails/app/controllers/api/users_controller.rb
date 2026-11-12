module Api
  class UsersController < ApplicationController
    def index
      render json: UserQuery.new.call
    end

    def show
      render json: User.find(params[:id])
    end

    def create
      render json: Users::Creator.new(params).call
    end

    def activate
      User.find(params[:id]).activate!
    end
  end
end
